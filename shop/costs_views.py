import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CostEntry, CostCentreProduct, CropBatch, FarmAsset, Product

MAX_ENTRIES = 200
MAX_ASSETS = 50

COST_CENTRE_RE = re.compile(r'^(crop|tree):[a-z0-9_-]+$')
FIXED_COST_CENTRES = {'livestock:goat', 'livestock:chicken', 'bees', 'vermi', 'water', 'shared'}


def _valid_cost_centre(value):
    return bool(value) and bool(COST_CENTRE_RE.match(value) or value in FIXED_COST_CENTRES)


# Every spelling ABMS might reasonably send for each field, keyed by the
# canonical (snake_case) name this view uses internally from here on.
# 'id'/'code'/'name'/'status'/'notes' are single words -- snake_case and
# camelCase coincide, so there's nothing to alias for them. The deployed
# ABMS client turned out to send plain snake_case throughout (abms_id,
# cost_centre, start_date) -- not just a casing difference on 'id': ABMS
# never sends a bare 'id' at all, only 'abms_id'/'abmsId' -- while this
# view's first draft only ever recognized 'id' and camelCase for the
# others, hence production's "Unknown field(s)" 400. Both spellings of
# any field this view might later grow are accepted the same way.
BATCH_FIELD_ALIASES = {
    'id': ('id', 'abms_id', 'abmsId'),
    'code': ('code',),
    'name': ('name',),
    'cost_centre': ('cost_centre', 'costCentre'),
    'start_date': ('start_date', 'startDate'),
    'end_date': ('end_date', 'endDate'),
    'status': ('status',),
    'notes': ('notes',),
}
BATCH_ALLOWED_KEYS = {alias for aliases in BATCH_FIELD_ALIASES.values() for alias in aliases}


def _normalize_batch_payload(body):
    """(normalized_dict, error_message). normalized_dict uses the
    canonical snake_case keys from BATCH_FIELD_ALIASES above, regardless
    of which spelling(s) the request actually used. error_message is None
    on success; on failure normalized_dict is None and error_message is
    the exact detail string CropBatchSyncView.post() should 400 with."""
    unknown = set(body.keys()) - BATCH_ALLOWED_KEYS
    if unknown:
        return None, f'Unknown field(s): {sorted(unknown)}.'

    normalized = {}
    for canonical, aliases in BATCH_FIELD_ALIASES.items():
        present = [(alias, body[alias]) for alias in aliases if alias in body]
        if not present:
            continue
        distinct_values = {value for _, value in present}
        if len(distinct_values) > 1:
            sent_as = ', '.join(alias for alias, _ in present)
            return None, f'{canonical} was sent as more than one field ({sent_as}) with different values.'
        normalized[canonical] = present[0][1]
    return normalized, None


class CropBatchSyncView(APIView):
    """POST /api/costs/batches/sync/ -- ABMS pushes one crop batch document
    here (create, rename, or close it). Same token auth + CORS as
    CostSyncView below -- see that class's own docstring for why neither
    view declares its own authentication_classes/permission_classes.

    Idempotent by abms_id, upserting a single batch per call (unlike
    CostSyncView's bulk entries/assets arrays -- ABMS only ever has one
    batch event to report at a time: create one, rename one, or close one).

    code is treated as immutable once a batch is first created: every
    CostEntry/InventoryMovement row referencing this batch does so by the
    literal code string, not by this row's pk or abms_id, so letting ABMS
    rename the code later would silently orphan every row already synced
    under the old one. ABMS can still rename the human-readable `name`
    freely -- "rename" in this feature's brief means that field, not code.

    product is NEVER taken from the request -- ABMS has no reason to know
    a Django product id. It's resolved automatically from CostCentreProduct:
    if exactly one product maps to this cost_centre, that becomes the
    batch's product; zero or multiple matches leave it null (CropBatch.
    product is nullable for exactly this reason). Re-resolved on every
    sync call, so mapping a product later (or remapping it) is reflected
    on the batch's next sync without ABMS needing to do anything.

    Accepts either spelling of every multi-word field (see
    BATCH_FIELD_ALIASES/_normalize_batch_payload above) -- ABMS's actual
    deployed client sends snake_case throughout."""

    def post(self, request):
        body = request.data
        if not isinstance(body, dict):
            return Response({'detail': 'Request body must be a JSON object.'}, status=400)

        normalized, error = _normalize_batch_payload(body)
        if error:
            return Response({'detail': error}, status=400)

        abms_id = normalized.get('id')
        if not abms_id:
            return Response({'detail': 'Missing id.'}, status=400)

        code = (normalized.get('code') or '').strip()
        if not code:
            return Response({'detail': 'Missing code.'}, status=400)

        cost_centre = normalized.get('cost_centre')
        if not _valid_cost_centre(cost_centre):
            return Response({'detail': f'Invalid cost_centre: {cost_centre!r}'}, status=400)

        start_date_str = normalized.get('start_date')
        start_date = parse_date(start_date_str) if isinstance(start_date_str, str) else None
        if not start_date:
            return Response({'detail': f'Invalid or missing start_date: {start_date_str!r}'}, status=400)

        end_date_str = normalized.get('end_date')
        end_date = parse_date(end_date_str) if isinstance(end_date_str, str) else None
        if end_date_str and not end_date:
            return Response({'detail': f'Invalid end_date: {end_date_str!r}'}, status=400)

        status_val = normalized.get('status') or 'open'
        if status_val not in ('open', 'closed'):
            return Response({'detail': f'Invalid status: {status_val!r}'}, status=400)

        name = (normalized.get('name') or '').strip()
        notes = normalized.get('notes') or ''

        with transaction.atomic():
            existing = CropBatch.objects.filter(abms_id=abms_id).first()
            if existing:
                if existing.code != code:
                    return Response({
                        'detail': f'code cannot be changed once set (this batch is {existing.code!r}) '
                                   '-- create a new batch instead.',
                    }, status=400)
                existing.name = name
                existing.cost_centre = cost_centre
                existing.product = self._resolve_product(cost_centre)
                existing.start_date = start_date
                existing.end_date = end_date
                existing.status = status_val
                existing.notes = notes
                existing.save()
                return Response({'code': existing.code, 'status': 'updated'})

            if CropBatch.objects.filter(code=code).exists():
                return Response({'detail': f'code {code!r} is already used by another batch.'}, status=400)

            batch = CropBatch.objects.create(
                abms_id=abms_id, code=code, name=name, cost_centre=cost_centre,
                product=self._resolve_product(cost_centre),
                start_date=start_date, end_date=end_date, status=status_val, notes=notes,
            )
            return Response({'code': batch.code, 'status': 'created'}, status=201)

    def _resolve_product(self, cost_centre):
        matches = list(CostCentreProduct.objects.filter(cost_centre=cost_centre).values_list('product_id', flat=True))
        if len(matches) == 1:
            return Product.objects.get(pk=matches[0])
        return None


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

        # Season/batch costing (shop/batch_report.py) -- optional, blank
        # means an ordinary unbatched cost. Soft per-item statuses, same
        # pattern as every other check in this method (CostSyncView's own
        # response contract is one 200 with a per-item result/count no
        # matter how many individual entries fail -- changing that just
        # for this one check would be a surprising, inconsistent carve-out).
        batch_code = raw.get('batchCode') or ''
        batch = None
        if batch_code:
            batch = CropBatch.objects.filter(code=batch_code).first()
            if not batch:
                return {'id': abms_id, 'kind': 'entry', 'status': 'batch_not_found', 'detail': f'Unknown batch_code: {batch_code!r}'}
            if batch.status == 'closed':
                return {'id': abms_id, 'kind': 'entry', 'status': 'batch_closed', 'detail': f'Batch {batch_code!r} is closed.'}

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
                and existing.batch_code == batch_code
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
            batch_code=batch_code,
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
