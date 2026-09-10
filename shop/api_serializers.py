from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from .models import InventoryMovement

# Movements created through this API must declare which app is writing them.
# 'admin' and 'website' movements are created elsewhere (Django admin, and
# eventually the checkout view) — the API is only for POS and ABMS.
ALLOWED_API_SOURCES = {'pos', 'abms'}


class InventoryMovementSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryMovement
        fields = ['id', 'product', 'variant', 'movement_type', 'source',
                  'quantity', 'related_order', 'note', 'created_at']
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
        instance = InventoryMovement(**data)
        try:
            instance.full_clean()
        except DjangoValidationError as e:
            raise serializers.ValidationError(e.message_dict)
        return data