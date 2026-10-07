import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CostEntry, FarmAsset

MAX_ENTRIES = 200
MAX_ASSETS = 50

COST_CENTRE_RE = re.compile(r'^(crop|tree):[a-z0-9_-]+$')
FIXED_COST_CENTRES = {'livestock:goat', 'livestock:chicken', 'bees', 'vermi', 'water', 'shared'}


def _valid_cost_centre(value):
    return bool(value) and bool(COST_CENTRE_RE.match(value) or value in FIXED_COST_CENTRES)


def _parse_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


class CostSyncView(APIView):
    """POST /api/costs/sync/ — ABMS (js/costs.js) pushes its costEntries/
    assets Firestore documents here. Deliberately placed next to, not
    under, /api/inventory/movements/'s /api/v1/ sibling: same token auth
    (DRF's global TokenAuthentication + IsAuthenticated — no override
    declared here, exactly like shop.api_views.InventoryMovementCreateView)
    and the same CORS_ALLOWED_ORIGINS entry for https://angan-baari.web.app
    (django-cors-headers applies that list to every path, not just
    /api/inventory/, since CORS_URLS_REGEX is unset) — no new secret, no
    new CORS config.

    Idempotent by each document's own abms_id. Each entry/asset is synced
    inside its own transaction.atomic() savepoint, so one malformed item
    in a batch can't roll back the rest — it just reports its own
    "error" status and the batch continues."""

    def post(self, request):
        body = request.data
        if not isinstance(body, dict):
            return Response({'detail': 'Request body must be a JSON object.'}, status=400)

        entries = body.get('entries', [])
        assets = body.get('assets', [])
        if not isinstance(entries, list) or not isinstance(assets, list):
            return Response({'detail': '"entries" and "assets" must be lists.'}, status=400)
        if len(entries) > MAX_ENTRIES:
            return Response({'detail': f'Too many entries in one batch (max {MAX_ENTRIES}).'}, status=400)
        if len(assets) > MAX_ASSETS:
            return Response({'detail': f'Too many assets in one batch (max {MAX_ASSETS}).'}, status=400)

        # Lets a reversal validly point at an original entry that's in the
        # SAME batch (not yet in the DB), not just one already synced.
        batch_entry_ids = {e.get('id') for e in entries if isinstance(e, dict) and e.get('id')}

        results = []
        counts = {}
        for raw in entries:
            result = self._sync_one_entry(raw, batch_entry_ids)
            results.append(result)
            counts[result['status']] = counts.get(result['status'], 0) + 1
        for raw in assets:
            result = self._sync_one_asset(raw)
            results.append(result)
            counts[result['status']] = counts.get(result['status'], 0) + 1

        return Response({'results': results, 'counts': counts})

    # ── cost entries ──────────────────────────────────────────────

    def _sync_one_entry(self, raw, batch_entry_ids):
        if not isinstance(raw, dict):
            return {'id': None, 'kind': 'entry', 'status': 'error', 'detail': 'Entry must be an object.'}
        abms_id = raw.get('id')
        if not abms_id:
            return {'id': None, 'kind': 'entry', 'status': 'error', 'detail': 'Missing id.'}
        try:
            with transaction.atomic():
                return self._sync_one_entry_inner(raw, abms_id, batch_entry_ids)
        except Exception as e:
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': str(e)}

    def _sync_one_entry_inner(self, raw, abms_id, batch_entry_ids):
        entry_type = raw.get('type') or 'cost'
        if entry_type not in ('cost', 'reversal'):
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': f"Invalid type: {entry_type!r}"}

        amount = _parse_decimal(raw.get('amount'))
        if amount is None:
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'Invalid or missing amount.'}
        if entry_type == 'cost' and amount <= 0:
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'A cost entry needs amount > 0.'}
        if entry_type == 'reversal':
            if amount == 0:
                return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'A reversal needs a non-zero amount.'}
            if amount > 0:
                # ABMS's own js/costs.js always sends a reversal already
                # negated; this only guards a batch that somehow didn't.
                amount = -amount

        date_str = raw.get('date')
        date_val = parse_date(date_str) if isinstance(date_str, str) else None
        if not date_val:
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': f"Invalid date: {date_str!r}"}

        cost_centre = raw.get('costCentre')
        if not _valid_cost_centre(cost_centre):
            return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': f"Invalid costCentre: {cost_centre!r}"}

        allocation_manual = raw.get('allocationManual')
        if allocation_manual is not None:
            if not isinstance(allocation_manual, dict) or not allocation_manual:
                return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'allocationManual must be a non-empty object.'}
            total_pct = Decimal('0')
            for pct in allocation_manual.values():
                parsed_pct = _parse_decimal(pct)
                if parsed_pct is None:
                    return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'allocationManual values must be numbers.'}
                total_pct += parsed_pct
            if abs(total_pct - Decimal('100')) > Decimal('0.01'):
                return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': f'allocationManual sums to {total_pct}, not 100.'}

        reversal_of = raw.get('reversalOf') or ''
        if entry_type == 'reversal':
            if not reversal_of:
                return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': 'A reversal needs reversalOf.'}
            original_known = (
                reversal_of in batch_entry_ids
                or CostEntry.objects.filter(abms_id=reversal_of).exists()
            )
            if not original_known:
                return {'id': abms_id, 'kind': 'entry', 'status': 'error', 'detail': f'original not found: {reversal_of}'}

        entered_by = raw.get('enteredBy')
        entered_by_email = entered_by.get('email', '') if isinstance(entered_by, dict) else ''

        abms_created_at = None
        created_raw = raw.get('createdAt')
        if isinstance(created_raw, str):
            abms_created_at = parse_datetime(created_raw)

        qty = _parse_decimal(raw.get('qty'))

        existing = CostEntry.objects.filter(abms_id=abms_id).first()
        if existing:
            unchanged = (
                existing.amount == amount
                and existing.date == date_val
                and existing.cost_centre == cost_centre
            )
            if unchanged:
                return {'id': abms_id, 'kind': 'entry', 'status': 'duplicate', 'detail': 'Already synced, unchanged.'}
            return {
                'id': abms_id, 'kind': 'entry', 'status': 'conflict',
                'detail': 'An entry with this id already exists with a different amount/date/costCentre — not overwritten (CostEntry is append-only).',
            }

        CostEntry.objects.create(
            abms_id=abms_id,
            date=date_val,
            amount=amount,
            entry_type=entry_type,
            reversal_of=reversal_of,
            cost_centre=cost_centre,
            category=raw.get('category') or '',
            qty=qty,
            unit=raw.get('unit') or '',
            supplier=raw.get('supplier') or '',
            note=raw.get('note') or '',
            is_shared=bool(raw.get('isShared')),
            allocation_rule=raw.get('allocationRule') or '',
            allocation_manual=allocation_manual,
            entered_by_email=entered_by_email,
            abms_created_at=abms_created_at,
        )
        return {'id': abms_id, 'kind': 'entry', 'status': 'created', 'detail': ''}

    # ── assets ────────────────────────────────────────────────────

    def _sync_one_asset(self, raw):
        if not isinstance(raw, dict):
            return {'id': None, 'kind': 'asset', 'status': 'error', 'detail': 'Asset must be an object.'}
        abms_id = raw.get('id')
        if not abms_id:
            return {'id': None, 'kind': 'asset', 'status': 'error', 'detail': 'Missing id.'}
        try:
            with transaction.atomic():
                return self._sync_one_asset_inner(raw, abms_id)
        except Exception as e:
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': str(e)}

    def _sync_one_asset_inner(self, raw, abms_id):
        name = (raw.get('name') or '').strip()
        if not name:
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': 'Missing name.'}

        cost = _parse_decimal(raw.get('cost'))
        if cost is None or cost <= 0:
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': 'Invalid or missing cost.'}

        purchase_date_str = raw.get('purchaseDate')
        purchase_date = parse_date(purchase_date_str) if isinstance(purchase_date_str, str) else None
        if not purchase_date:
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': f"Invalid purchaseDate: {purchase_date_str!r}"}

        try:
            life_years = int(raw.get('lifeYears'))
        except (TypeError, ValueError):
            life_years = None
        if not life_years or life_years <= 0:
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': 'Invalid or missing lifeYears.'}

        salvage_value = _parse_decimal(raw.get('salvageValue'))
        if salvage_value is None:
            salvage_value = Decimal('0')

        cost_centre = raw.get('costCentre')
        if not _valid_cost_centre(cost_centre):
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': f"Invalid costCentre: {cost_centre!r}"}

        status_val = raw.get('status') or 'active'
        if status_val not in ('active', 'disposed'):
            return {'id': abms_id, 'kind': 'asset', 'status': 'error', 'detail': f"Invalid status: {status_val!r}"}

        disposed_date_str = raw.get('disposedDate')
        disposed_date = parse_date(disposed_date_str) if isinstance(disposed_date_str, str) else None

        existing = FarmAsset.objects.filter(abms_id=abms_id).first()
        if existing:
            existing.name = name
            existing.asset_category = raw.get('assetCategory') or existing.asset_category
            existing.life_years = life_years
            existing.salvage_value = salvage_value
            existing.status = status_val
            existing.disposed_date = disposed_date
            existing.save()
            return {'id': abms_id, 'kind': 'asset', 'status': 'updated', 'detail': ''}

        FarmAsset.objects.create(
            abms_id=abms_id,
            name=name,
            asset_category=raw.get('assetCategory') or '',
            purchase_date=purchase_date,
            cost=cost,
            life_years=life_years,
            salvage_value=salvage_value,
            cost_centre=cost_centre,
            status=status_val,
            disposed_date=disposed_date,
        )
        return {'id': abms_id, 'kind': 'asset', 'status': 'created', 'detail': ''}
