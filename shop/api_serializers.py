from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import CropBatch, InventoryMovement, Product

# Movements created through this API must declare which app is writing them.
# 'admin' and 'website' movements are created elsewhere (Django admin, and
# eventually the checkout view) — the API is only for POS and ABMS.
ALLOWED_API_SOURCES = {'pos', 'abms'}


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

    class Meta:
        model = InventoryMovement
        fields = ['id', 'product', 'variant', 'movement_type', 'source',
                  'quantity', 'related_order', 'note', 'batch_code', 'created_at']
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

        if batch_code:
            if data.get('movement_type') != 'harvest':
                raise serializers.ValidationError(
                    {'batch_code': 'batch_code is only valid for harvest movements.'}
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
        elif not product:
            raise serializers.ValidationError({'product': ['This field is required.']})

        instance = InventoryMovement(**data)
        try:
            instance.full_clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return data