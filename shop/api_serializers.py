from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import CropBatch, InventoryMovement, Product

# Movements created through this API must declare which app is writing them.
# 'admin' and 'website' movements are created elsewhere (Django admin, and
# eventually the checkout view) — the API is only for POS and ABMS.
ALLOWED_API_SOURCES = {'pos', 'abms'}

# A reversal (reversal_of_abms_id set) may only use one of these movement_type
# combinations against the original it's reversing -- adjustment_remove undoes
# a harvest, adjustment_add undoes a waste. Any other combination (including
# trying to reverse a reversal, or reversing the wrong original type) is
# rejected by the REVERSAL_TYPE_MAP.get(...) != original.movement_type check
# in _validate_reversal below.
REVERSAL_TYPE_MAP = {
    'adjustment_remove': 'harvest',
    'adjustment_add': 'waste',
}

# Machine-readable codes InventoryMovementCreateView looks for on a non-2xx
# response to an abms_id/reversal_of_abms_id request (see
# Angan-Baari-Knowledge/06-API/ABMS-Integration.md for the full contract).
# Passed as the `code=` kwarg on serializers.ValidationError so every
# resulting ErrorDetail carries it (DRF's _get_error_details() propagates
# a dict/list ValidationError's `code` onto every leaf) -- the view then
# walks serializer.errors looking for one of these rather than
# string-matching message text.
CODE_ORIGINAL_NOT_FOUND = 'original_not_found'
CODE_OVER_REVERSAL = 'over_reversal'
CODE_INVALID_REVERSAL = 'invalid_reversal'
CODE_INSUFFICIENT_STOCK = 'insufficient_stock'


class BatchClosedError(APIException):
    """A real 409, not a 400 -- DRF's ValidationError always maps to 400,
    so a closed-batch rejection (season/batch costing, shop/batch_report.py)
    needs this distinct APIException subclass instead. Raising it from
    inside validate() still propagates correctly through DRF's normal
    is_valid(raise_exception=True) flow, since DRF's exception handler
    checks for APIException generally, not specifically ValidationError."""
    status_code = 409
    default_detail = 'This batch is closed.'
    default_code = 'batch_closed'


class InventoryMovementSerializer(serializers.ModelSerializer):
    # Declared explicitly (not left to ModelSerializer's auto-generation)
    # so it can be made optional ONLY when batch_code resolves it instead --
    # see validate() below, which puts the "actually required unless a
    # batch_code supplies it" rule back in by hand.
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all(), required=False, allow_null=True)
    # Both optional -- a plain call (no abms_id) behaves exactly as before.
    # abms_id is the ABMS-sync idempotency key (see InventoryMovementCreateView);
    # reversal_of_abms_id, when set, points at the abms_id of an earlier
    # harvest/waste this movement undoes (see _validate_reversal below).
    abms_id = serializers.CharField(max_length=64, required=False, allow_null=True, allow_blank=True)
    reversal_of_abms_id = serializers.CharField(max_length=64, required=False, allow_blank=True)

    class Meta:
        model = InventoryMovement
        fields = ['id', 'product', 'variant', 'movement_type', 'source',
                  'quantity', 'related_order', 'note', 'batch_code', 'created_at',
                  'abms_id', 'reversal_of_abms_id']
        read_only_fields = ['id', 'created_at']

    def validate_source(self, value):
        if value not in ALLOWED_API_SOURCES:
            raise serializers.ValidationError(
                f"API requests must set source to one of {sorted(ALLOWED_API_SOURCES)}."
            )
        return value

    def validate(self, data):
        """Re-run every rule already defined on InventoryMovement.clean()
        (quantity must be 1 for fixed-weight products, can't remove more
        stock than exists) by building the real instance and calling
        full_clean() on it. DRF's ModelSerializer does NOT call the model's
        clean() automatically — skipping this step would mean the API
        bypasses the exact protections that Django admin gets for free."""
        batch_code = data.get('batch_code') or ''
        product = data.get('product')
        reversal_of_abms_id = data.get('reversal_of_abms_id') or ''

        if batch_code:
            # Originally "harvest only" -- loosened to also allow a
            # batch_code on the two reversal types, since a harvest reversal
            # (adjustment_remove) or waste reversal (adjustment_add) must be
            # able to carry/match the same batch_code as the original it's
            # reversing (see _validate_reversal). This also means the
            # closed-batch check two lines down now applies to those
            # reversal types too, not just plain harvests.
            if data.get('movement_type') not in ('harvest', 'adjustment_remove', 'adjustment_add'):
                raise serializers.ValidationError(
                    {'batch_code': 'batch_code is only valid for harvest movements, or an '
                                    'adjustment_remove/adjustment_add reversing one.'}
                )
            batch = CropBatch.objects.filter(code=batch_code).first()
            if not batch:
                raise serializers.ValidationError({'batch_code': f'Unknown batch_code: {batch_code!r}'})
            if batch.status == 'closed':
                raise BatchClosedError(f'Batch {batch_code!r} is closed.')

            # batch.product is the one source of truth for "which product
            # does this cost centre's harvest belong to" -- checked before
            # looking at whatever `product` the request itself supplied,
            # since there's otherwise no way to confirm a supplied product
            # actually belongs to this batch's cost centre at all.
            if not batch.product_id:
                raise serializers.ValidationError({
                    'batch_code': f'No product is mapped to cost centre {batch.cost_centre}. '
                                   'Add it in Admin > Cost centre products.',
                })
            if product and product.id != batch.product_id:
                raise serializers.ValidationError(
                    {'batch_code': "This harvest's product does not match the batch's own product."}
                )
            if not product:
                # ABMS doesn't need to know a Django product id for a
                # batched harvest -- the batch's own product resolves it,
                # same reasoning as CropBatchSyncView's own product
                # auto-resolution (shop/costs_views.py).
                data['product'] = batch.product
        elif not product and not reversal_of_abms_id:
            # A reversal with no batch_code can still derive its product
            # from the original it reverses (see _validate_reversal) --
            # only a plain, non-reversal, non-batched movement requires
            # product upfront.
            raise serializers.ValidationError({'product': ['This field is required.']})

        if reversal_of_abms_id:
            data = self._validate_reversal(data, reversal_of_abms_id)

        instance = InventoryMovement(**data)
        try:
            # abms_id's own uniqueness is excluded here -- the VIEW is what
            # decides what a reused abms_id means (duplicate/conflict/a
            # genuine race), via its own existing-row lookup before this
            # validate() call and its IntegrityError catch around save().
            # Letting Django's own validate_unique() raise on it here would
            # reject every retry before the view ever got a chance to tell
            # an identical resend apart from a genuine conflict.
            instance.full_clean(exclude=['abms_id'])
        except DjangoValidationError as e:
            message_dict = e.message_dict
            # The one DjangoValidationError shape that gets its own
            # machine-readable code: InventoryMovement.clean()'s
            # stock-negative check, identified by its own exact wording
            # (shop/models.py) since Django's message_dict carries no
            # structured code of its own. Every other clean() failure
            # (e.g. "fixed-weight quantity must be 1") stays uncoded.
            quantity_msgs = message_dict.get('quantity') or []
            if any('currently available' in str(m) for m in quantity_msgs):
                raise serializers.ValidationError(message_dict, code=CODE_INSUFFICIENT_STOCK)
            raise serializers.ValidationError(message_dict)
        return data

    def _validate_reversal(self, data, reversal_of_abms_id):
        """reversal_of_abms_id set: this movement claims to reverse an
        earlier ABMS-sourced harvest/waste. source must be 'abms' (only
        ABMS posts reversals), the original must exist (also source='abms'),
        the movement_type combination must be one of REVERSAL_TYPE_MAP, and
        product/variant/batch_code must match the original -- derived from
        it when the request omits them, rejected when they disagree. Total
        reversed quantity against one original can never exceed the
        original's own quantity. Stock-negative safety for the reversal
        itself is NOT checked here -- it's left to the instance.full_clean()
        call at the end of validate(), the same stock check every other
        movement already goes through."""
        if data.get('source') != 'abms':
            raise serializers.ValidationError(
                {'reversal_of_abms_id': ["Reversals must have source='abms'."]},
                code=CODE_INVALID_REVERSAL,
            )

        original = InventoryMovement.objects.filter(
            abms_id=reversal_of_abms_id, source='abms'
        ).first()
        if not original:
            raise serializers.ValidationError(
                {'reversal_of_abms_id': ['original not found']},
                code=CODE_ORIGINAL_NOT_FOUND,
            )

        movement_type = data.get('movement_type')
        if REVERSAL_TYPE_MAP.get(movement_type) != original.movement_type:
            raise serializers.ValidationError({
                'movement_type': [f"A {movement_type!r} movement cannot reverse a "
                                   f"{original.movement_type!r} movement."],
            }, code=CODE_INVALID_REVERSAL)

        product = data.get('product')
        if product is None:
            data['product'] = original.product
        elif product.id != original.product_id:
            raise serializers.ValidationError(
                {'product': ["This reversal's product does not match the original's product."]},
                code=CODE_INVALID_REVERSAL,
            )

        variant = data.get('variant')
        if variant is None:
            data['variant'] = original.variant
        elif variant.id != original.variant_id:
            raise serializers.ValidationError(
                {'variant': ["This reversal's variant does not match the original's variant."]},
                code=CODE_INVALID_REVERSAL,
            )

        batch_code = data.get('batch_code') or ''
        if not batch_code:
            data['batch_code'] = original.batch_code
        elif batch_code != original.batch_code:
            raise serializers.ValidationError(
                {'batch_code': ["This reversal's batch_code does not match the original's batch_code."]},
                code=CODE_INVALID_REVERSAL,
            )

        # Exclude this exact request's own abms_id from the "already
        # reversed" sum -- otherwise a duplicate retry of the SAME reversal
        # (which has already landed once) would count its own earlier copy
        # against itself and look like an over-reversal. A request with no
        # abms_id has no "earlier copy" to exclude, so the sum is taken as-is.
        this_abms_id = data.get('abms_id') or None
        reversals_qs = InventoryMovement.objects.filter(reversal_of_abms_id=reversal_of_abms_id)
        if this_abms_id:
            reversals_qs = reversals_qs.exclude(abms_id=this_abms_id)
        already_reversed = reversals_qs.aggregate(total=Sum('quantity'))['total'] or Decimal('0')

        requested_quantity = data.get('quantity') or Decimal('0')
        if already_reversed + requested_quantity > original.quantity:
            remaining = original.quantity - already_reversed
            raise serializers.ValidationError({
                'quantity': [f'Cannot reverse {requested_quantity} -- only {remaining} of the '
                             f'original {original.quantity} remains unreversed.'],
            }, code=CODE_OVER_REVERSAL)

        return data
