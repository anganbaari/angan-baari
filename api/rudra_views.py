"""Read-only endpoints for the Rudra assistant (GET /api/rudra/...).

These are thin subclasses of the existing staff Reports views in
api/views.py: same parameter parsing, same shop/reports.py and
shop/pl_report.py calls, same numbers as the /dashboard/ -- only the
authentication differs (bearer token, see IsRudraToken) and every response
carries a `caveats` list. Rudra must relay those caveats whenever it quotes
sales or P&L figures; they are returned by the server so the wording can't
drift from what the data actually is.

Nothing here writes anything. CSV export is not offered.
"""
from django.utils import timezone as djtz
from rest_framework.response import Response
from rest_framework.views import APIView

from shop.models import Product, ProductOrder
from shop.reports import ALERT_ORDER_PENDING_DAYS, LOCAL_TZ
from shop.stock import get_stock_table_rows

from .permissions import IsRudraToken
from .views import (
    ReportPaymentsView, ReportPLView, ReportProductsView, ReportSummaryView,
)

POS_ONLY_CAVEAT = (
    'Revenue is POS-only. Website orders have no stored price, so they are '
    'reported as counts, never as Rs.'
)
ESTIMATE_CAVEAT = (
    'Per-product and per-category revenue is an estimate: historical lines are '
    "priced at today's price and offer/combo discounts are not preserved. "
    'Sale-level totals and cash-vs-digital payment splits are real.'
)
PL_CAVEAT = (
    'The P&L does not include website-order revenue. Sales from before line '
    'tracking are reported separately.'
)


class RudraReadMixin:
    authentication_classes = []
    permission_classes = [IsRudraToken]
    caveats = (POS_ONLY_CAVEAT,)

    def get(self, request, *args, **kwargs):
        if request.query_params.get('export'):
            return Response({'status': 'error', 'message': 'Export is not available on this API.'}, status=400)
        response = super().get(request, *args, **kwargs)
        if isinstance(response, Response) and response.status_code == 200 and isinstance(response.data, dict):
            response.data['caveats'] = list(self.caveats)
        return response


class RudraSalesSummaryView(RudraReadMixin, ReportSummaryView):
    """GET /api/rudra/sales/summary/ -- KPIs, top products (estimated
    revenue), channel split, payment mix and trend. Same params as
    /api/v1/reports/summary/ (start, end, channel, granularity, compare)."""
    caveats = (POS_ONLY_CAVEAT, ESTIMATE_CAVEAT)


class RudraPaymentsView(RudraReadMixin, ReportPaymentsView):
    """GET /api/rudra/sales/payments/ -- cash vs digital split (real figures)."""


class RudraProductsView(RudraReadMixin, ReportProductsView):
    """GET /api/rudra/sales/products/ -- per-product / per-category table."""
    caveats = (POS_ONLY_CAVEAT, ESTIMATE_CAVEAT)


class RudraPLView(RudraReadMixin, ReportPLView):
    """GET /api/rudra/pl/ -- Profit & Loss. Accepts the same params as
    /api/v1/reports/pl/: from+to, bs_year[+bs_month], or fiscal_year;
    defaults to the current B.S. month."""
    caveats = (POS_ONLY_CAVEAT, PL_CAVEAT)


LOW_STOCK_CAVEAT = (
    'Low means strictly below the low-stock threshold set for each product (the same rule as the POS stock screen and the '
    'low-stock Telegram alert). Goats and chickens are tracked per animal and are not covered.'
)
PENDING_ORDERS_CAVEAT = (
    'Website orders have no stored price, so they are listed, never valued in Rs. "Open" means pending or confirmed '
    '(not yet delivered or cancelled).'
)
MAX_OPEN_ORDERS = 50


class RudraLowStockView(APIView):
    """GET /api/rudra/stock/low/ -- products below their low-stock threshold (including sold-out ones), most urgent first. Reuses
    shop.stock.get_stock_table_rows(), the one implementation behind the POS stock screen and the Telegram alert."""
    authentication_classes = []
    permission_classes = [IsRudraToken]

    def get(self, request):
        # No is_available filter: Product.is_available is switched off automatically when stock reaches 0 (shop/signals.py),
        # so filtering on it would hide exactly the sold-out products. The POS stock screen and the alert do not filter either.
        rows = [r for r in get_stock_table_rows() if r['is_low']]
        modes = dict(Product.objects.filter(id__in=[r['product_id'] for r in rows]).values_list('id', 'pricing_mode'))
        items = [{
            'product_id': r['product_id'], 'name': r['name'],
            'current_stock': float(r['current_stock']), 'low_stock_threshold': r['low_stock_threshold'],
            'unit': 'kg' if modes.get(r['product_id']) == 'variable_weight' else 'pcs',
            'restock_method': r['restock_method'],
            'last_restocked_at': r['last_restocked_at'].astimezone(LOCAL_TZ).date().isoformat() if r['last_restocked_at'] else None,
        } for r in rows]
        return Response({'count': len(items), 'items': items, 'caveats': [LOW_STOCK_CAVEAT]})


class RudraPendingOrdersView(APIView):
    """GET /api/rudra/orders/pending/ -- website orders still open (pending or confirmed), oldest first, each with the
    days it has waited. `stale` uses the dashboard's own threshold (ALERT_ORDER_PENDING_DAYS). Customer contact
    details are deliberately not included."""
    authentication_classes = []
    permission_classes = [IsRudraToken]

    def get(self, request):
        now = djtz.now()
        open_orders = ProductOrder.objects.filter(status__in=('pending', 'confirmed')).order_by('ordered_at')
        total = open_orders.count()
        items = [{
            'order_number': o.order_number, 'name': o.name, 'status': o.status,
            'product_interest': o.product_interest,
            'ordered_at': o.ordered_at.astimezone(LOCAL_TZ).isoformat(),
            'days_pending': (now - o.ordered_at).days,
            'stale': (now - o.ordered_at).days >= ALERT_ORDER_PENDING_DAYS,
        } for o in open_orders[:MAX_OPEN_ORDERS]]
        return Response({'count': total, 'shown': len(items), 'stale_count': sum(i['stale'] for i in items),
                         'stale_after_days': ALERT_ORDER_PENDING_DAYS, 'items': items,
                         'caveats': [PENDING_ORDERS_CAVEAT]})
