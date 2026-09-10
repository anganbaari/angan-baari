from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Product, ProductVariant, InventoryMovement
from .api_serializers import InventoryMovementSerializer


class InventoryMovementCreateView(generics.CreateAPIView):
    """POST a new inventory movement (harvest / sale / waste / return /
    adjustment). Used by the POS app and ABMS. 'source' in the request body
    must be 'pos' or 'abms' — enforced in the serializer.

    The response includes the resulting current_stock for the affected
    product/variant, so the calling app (e.g. the POS screen) doesn't need
    a second request just to refresh the number it's showing."""
    queryset = InventoryMovement.objects.all()
    serializer_class = InventoryMovementSerializer

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        movement = InventoryMovement.objects.get(pk=response.data['id'])
        response.data['current_stock'] = str(
            InventoryMovement.current_stock(movement.product, variant=movement.variant)
        )
        return response


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