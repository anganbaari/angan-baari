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
from rest_framework.response import Response

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
