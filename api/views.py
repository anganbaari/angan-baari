import secrets
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import generics, permissions, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.emails import send_order_received_email
from shop.models import Category, InventoryMovement, POSSale, Product, ProductOrder
from shop.views import create_inventory_movements_from_snapshot, create_order_inventory_movements

from .permissions import IsStaffUser
from .serializers import (
    CategorySerializer,
    InventoryMovementReadSerializer,
    OrderCreateSerializer,
    OrderDetailSerializer,
    OrderStatusSerializer,
    POSSaleCreateSerializer,
    POSSaleReadSerializer,
    ProductSerializer,
)


# ─── PUBLIC READ ────────────────────────────────────────────────

class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = Product.objects.select_related('category').prefetch_related('variants').order_by('name')
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        return qs


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.select_related('category').prefetch_related('variants')
    serializer_class = ProductSerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    authentication_classes = []
    permission_classes = [permissions.AllowAny]


# ─── STAFF-ONLY READ ────────────────────────────────────────────

class InventoryMovementListView(generics.ListAPIView):
    """Separate v1 GET-capable view onto the ledger. The original
    /api/inventory/movements/ (shop/api_urls.py) only supports POST, is
    token-authed for ABMS, and is untouched by this app."""

    serializer_class = InventoryMovementReadSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get_queryset(self):
        qs = InventoryMovement.objects.select_related('product', 'variant').order_by('-created_at')
        product_id = self.request.query_params.get('product')
        movement_type = self.request.query_params.get('movement_type')
        source = self.request.query_params.get('source')
        if product_id:
            qs = qs.filter(product_id=product_id)
        if movement_type:
            qs = qs.filter(movement_type=movement_type)
        if source:
            qs = qs.filter(source=source)
        return qs


# ─── POS SALES (staff-only list + create) ──────────────────────

class POSSaleView(generics.ListAPIView):
    """GET: sale history (staff-only). POST: create a sale via the API —
    same server-side total recomputation, same
    create_inventory_movements_from_snapshot(strict=True) call inside
    transaction.atomic(), and the same client_sale_id idempotency as
    pos_create_sale() in shop/views.py, which templates/pos.html still
    calls directly and unchanged."""

    queryset = POSSale.objects.select_related('cashier').order_by('-created_at')
    serializer_class = POSSaleReadSerializer
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def post(self, request, *args, **kwargs):
        input_serializer = POSSaleCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        client_sale_id = data['client_sale_id']

        existing = POSSale.objects.filter(client_sale_id=client_sale_id).first()
        if existing:
            return Response({
                'status': 'ok',
                'sale_number': existing.sale_number,
                'total': str(existing.total_amount),
            })

        cart = data['cart']
        payment_method = data['payment_method']
        if payment_method not in dict(POSSale.PAYMENT_METHOD_CHOICES):
            return Response({'status': 'error', 'message': 'Choose a payment method.'}, status=400)

        total = Decimal('0')
        cart_snapshot = []
        try:
            for line in cart:
                product = Product.objects.get(id=line.get('product_id'), is_available=True)
                qty = int(line.get('qty', 1) or 1)
                weight = line.get('weight')
                variant_id = line.get('variant_id')

                if product.pricing_mode == 'fixed_weight':
                    variant = (
                        product.variants.filter(id=variant_id, is_available=True).first()
                        if variant_id else None
                    )
                    if not variant:
                        return Response(
                            {'status': 'error', 'message': f'{product.name}: that animal is no longer available.'},
                            status=400,
                        )
                    line_total = variant.total_price()
                elif product.pricing_mode == 'variable_weight':
                    line_total = Decimal(str(product.price)) * Decimal(str(weight or 0))
                else:
                    line_total = Decimal(str(product.price)) * qty

                total += line_total
                cart_snapshot.append({
                    'product_id': product.id, 'weight': weight, 'qty': qty, 'variant_id': variant_id,
                })
        except (Product.DoesNotExist, InvalidOperation, TypeError, ValueError):
            return Response({'status': 'error', 'message': 'One of the items in this cart is no longer valid.'}, status=400)

        try:
            with transaction.atomic():
                sale = POSSale.objects.create(
                    client_sale_id=client_sale_id,
                    cashier=request.user,
                    payment_method=payment_method,
                    cart_snapshot=cart_snapshot,
                    total_amount=total,
                )
                create_inventory_movements_from_snapshot(
                    cart_snapshot, 'sale', source='pos',
                    related_pos_sale=sale, note=f"POS sale {sale.sale_number} (API)",
                    strict=True,
                )
        except DjangoValidationError as e:
            message = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
            return Response({'status': 'error', 'message': f'Not enough stock: {message}'}, status=409)
        except Exception:
            return Response(
                {'status': 'error', 'message': 'Could not complete this sale — nothing was charged or recorded. Please try again.'},
                status=400,
            )

        return Response({'status': 'ok', 'sale_number': sale.sale_number, 'total': str(total)}, status=status.HTTP_201_CREATED)


# ─── WEBSITE ORDERS (public, guest checkout) ───────────────────

class OrderCreateView(APIView):
    """Public — matches current checkout() behavior (no login required).
    Calls create_order_inventory_movements(), which itself calls
    create_inventory_movements_from_snapshot(strict=False): bad lines are
    silently skipped rather than losing the whole order, same as the
    website. Coupon/offer pricing isn't part of this v1 surface."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        input_serializer = OrderCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        items_desc = []
        cart_snapshot = []
        for line in data['cart']:
            try:
                product = Product.objects.get(id=line.get('product_id'), is_available=True)
            except (Product.DoesNotExist, TypeError, ValueError):
                continue

            try:
                qty = max(1, int(line.get('qty') or 1))
            except (TypeError, ValueError):
                qty = 1
            weight = line.get('weight')
            variant_id = line.get('variant_id')

            cart_snapshot.append({
                'product_id': product.id, 'weight': weight, 'qty': qty, 'variant_id': variant_id,
            })
            items_desc.append(f"{product.name} ({weight}kg) x{qty}" if weight else f"{product.name} x{qty}")

        if not cart_snapshot:
            return Response({'status': 'error', 'message': 'No valid items in cart.'}, status=400)

        order = ProductOrder.objects.create(
            name=data['name'],
            email=data['email'],
            phone=data.get('phone', ''),
            address=data.get('address', ''),
            product_interest=', '.join(items_desc),
            message=data.get('message', ''),
            cart_snapshot=cart_snapshot,
        )
        create_order_inventory_movements(order, 'sale')

        try:
            send_order_received_email(order)
        except Exception:
            pass

        return Response(
            {'status': 'ok', 'order_number': order.order_number, 'id': order.id},
            status=status.HTTP_201_CREATED,
        )


class OrderDetailView(APIView):
    """Public. Returns the full order (name/email/phone/address/
    cart_snapshot) only when the caller proves ownership via
    ?token=<cancel_token> — the same secret already emailed to the
    customer on send_order_received_email and used by the existing
    /cancel/<token>/ link. Without a matching token, falls back to the
    status-only fields (order_number/status/ordered_at/product_interest),
    so a bare guessable integer id can never be used to enumerate other
    customers' PII."""

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def get(self, request, pk, *args, **kwargs):
        try:
            order = ProductOrder.objects.get(pk=pk)
        except ProductOrder.DoesNotExist:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        token = request.query_params.get('token', '')
        if token and order.cancel_token and secrets.compare_digest(token, order.cancel_token):
            serializer = OrderDetailSerializer(order)
        else:
            serializer = OrderStatusSerializer(order)
        return Response(serializer.data)
