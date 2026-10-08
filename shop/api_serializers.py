from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import CropBatch, InventoryMovement

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
            product = data.get('product')
            if batch.product_id and product and batch.product_id != product.id:
                raise serializers.ValidationError(
                    {'batch_code': "This harvest's product does not match the batch's own product."}
                )

        instance = InventoryMovement(**data)
        try:
            instance.full_clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return data