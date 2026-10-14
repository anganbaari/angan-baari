from django.db import IntegrityError, transaction
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Product, ProductVariant, InventoryMovement
from .api_serializers import BatchClosedError, InventoryMovementSerializer

# The exact set of machine-readable codes InventoryMovementSerializer tags
# onto its ValidationErrors for the abms_id/reversal_of_abms_id path (see
# Angan-Baari-Knowledge/06-API/ABMS-Integration.md). 'abms_id_conflict' and
# 'batch_closed' aren't in this set -- the first is decided directly by
# this view (not a serializer validation error at all), the second is a
# distinct APIException caught separately below.
KNOWN_ERROR_CODES = {'original_not_found', 'over_reversal', 'invalid_reversal', 'insufficient_stock'}


class InventoryMovementCreateView(generics.CreateAPIView):
    """POST a new inventory movement (harvest / sale / waste / return /
    adjustment). Used by the POS app and ABMS. 'source' in the request body
    must be 'pos' or 'abms' — enforced in the serializer.

    The response includes the resulting current_stock for the affected
    product/variant, so the calling app (e.g. the POS screen) doesn't need
    a second request just to refresh the number it's showing.

    Optional abms_id / reversal_of_abms_id (see
    Angan-Baari-Knowledge/06-API/ABMS-Integration.md for the full contract):
    a request with NO abms_id behaves exactly as before -- plain create,
    201, no dedup/idempotency handling at all, and this is the ONLY path
    POS ever uses. A request WITH an abms_id gets retry-safe idempotency
    and conflict detection, mirroring CostEntry/CostSyncView
    (shop/costs_views.py) -- this is for ABMS harvest/waste sync."""
    queryset = InventoryMovement.objects.all()
    serializer_class = InventoryMovementSerializer

    def create(self, request, *args, **kwargs):
        abms_id = (request.data.get('abms_id') or '').strip() or None
        if not abms_id:
            response = super().create(request, *args, **kwargs)
            movement = InventoryMovement.objects.get(pk=response.data['id'])
            response.data['current_stock'] = str(
                InventoryMovement.current_stock(movement.product, variant=movement.variant)
            )
            return response

        return self._create_with_abms_id(request, abms_id)

    def _create_with_abms_id(self, request, abms_id):
        existing = self._find_existing(abms_id)

        serializer = self.get_serializer(data=request.data)
        try:
            is_valid = serializer.is_valid()
        except BatchClosedError as exc:
            # Not a ValidationError, so is_valid() doesn't catch it on its
            # own -- caught here (rather than left to propagate to DRF's
            # default exception handling, as the plain no-abms_id path
            # above still does) specifically so this path's response gets
            # the same {"status", "code", "message"} shape as every other
            # abms_id/reversal_of_abms_id failure.
            return Response({
                'status': 'error', 'code': 'batch_closed', 'message': str(exc.detail),
            }, status=status.HTTP_409_CONFLICT)

        if not is_valid:
            return self._error_response(serializer.errors)

        validated = serializer.validated_data
        if existing:
            if self._same_content(existing, validated):
                return self._movement_response(existing, 'duplicate', status.HTTP_200_OK)
            return self._movement_response(
                existing, 'conflict', status.HTTP_409_CONFLICT, code='abms_id_conflict'
            )

        try:
            with transaction.atomic():
                movement = serializer.save()
        except IntegrityError:
            # Lost a race against a concurrent identical request that
            # inserted the same abms_id between the existence check above
            # and this save -- the other request's row is now the real one.
            movement = InventoryMovement.objects.get(abms_id=abms_id)
            return self._movement_response(movement, 'duplicate', status.HTTP_200_OK)

        return self._movement_response(movement, 'created', status.HTTP_201_CREATED)

    @staticmethod
    def _find_existing(abms_id):
        return InventoryMovement.objects.filter(abms_id=abms_id).first()

    @staticmethod
    def _same_content(existing, validated):
        """The exact field list the ABMS-sync contract names for deciding
        duplicate vs conflict -- deliberately excludes source/note/
        related_order/unit_cost. Compared against validated_data (so
        anything batch_code/reversal derived -- e.g. an omitted product
        resolved from the batch or the original) is compared on its
        resolved value, not the raw request shape."""
        product = validated.get('product')
        variant = validated.get('variant')
        return (
            existing.product_id == (product.id if product else None)
            and existing.variant_id == (variant.id if variant else None)
            and existing.movement_type == validated.get('movement_type')
            and existing.quantity == validated.get('quantity')
            and (existing.batch_code or '') == (validated.get('batch_code') or '')
            and (existing.reversal_of_abms_id or '') == (validated.get('reversal_of_abms_id') or '')
        )

    @staticmethod
    def _movement_response(movement, status_label, http_status, code=None):
        data = InventoryMovementSerializer(movement).data
        data['status'] = status_label
        if code:
            data['code'] = code
        data['current_stock'] = str(
            InventoryMovement.current_stock(movement.product, variant=movement.variant)
        )
        return Response(data, status=http_status)

    @staticmethod
    def _error_response(errors):
        # serializer.errors is a dict of field -> ErrorDetail (or a list of
        # them). InventoryMovementSerializer tags every ErrorDetail it
        # raises for a known abms_id/reversal_of_abms_id failure mode with
        # a `code` (see KNOWN_ERROR_CODES above and
        # shop/api_serializers.py) -- walking for that is more robust than
        # matching on message text, and survives wording changes.
        for value in errors.values():
            items = value if isinstance(value, list) else [value]
            for item in items:
                code = getattr(item, 'code', None)
                if code in KNOWN_ERROR_CODES:
                    return Response({
                        'status': 'error', 'code': code, 'message': str(item),
                    }, status=status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'error', 'message': errors}, status=status.HTTP_400_BAD_REQUEST)


class CurrentStockView(APIView):
    """GET /api/inventory/stock/<product_id>/
    GET /api/inventory/stock/<product_id>/?variant=<variant_id>
    Returns current stock for a product, or for one specific variant
    (fixed-weight animals)."""

    def get(self, request, product_id):
        try:
            product = Product.objects.get(pk=product_id)
        except Product.DoesNotExist:
            return Response({'detail': 'Product not found.'}, status=status.HTTP_404_NOT_FOUND)

        variant = None
        variant_id = request.query_params.get('variant')
        if variant_id:
            try:
                variant = ProductVariant.objects.get(pk=variant_id, product=product)
            except ProductVariant.DoesNotExist:
                return Response({'detail': 'Variant not found for this product.'}, status=status.HTTP_404_NOT_FOUND)

        stock = InventoryMovement.current_stock(product, variant=variant)
        return Response({
            'product': product.id,
            'variant': variant.id if variant else None,
            'current_stock': str(stock),
        })
