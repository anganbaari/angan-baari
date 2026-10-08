'use strict';
/* Staff Reports & Dashboard (/dashboard/) -- vanilla JS, no build step.
   Talks only to the read-only GET /api/v1/reports/* endpoints; never
   calls anything that writes to the POS, checkout, or inventory ledger.
   This page is never cached (no service worker here) and has no offline
   mode -- a fetch failure shows a plain "needs a connection" message.

   All dates shown on this page are Bikram Sambat (B.S.) -- see
   static/js/bs-calendar.js (mirrors shop/bs_calendar.py's data table,
   itself ported from templates/pos.html's inline converter). The API
   underneath still takes/returns plain A.D. ISO dates; this file is
   where that conversion happens, at the UI boundary.
*/

const PALETTE = ['#c9a84c', '#5fa373', '#7fb8d6', '#e0735a', '#b68fd6', '#e8cc84', '#8fa391', '#d6a67f'];
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (window.Chart) {
    Chart.defaults.font.family = "'DM Sans', sans-serif";
    Chart.defaults.color = '#9aab96';
    Chart.defaults.borderColor = 'rgba(255,255,255,0.08)';
    if (REDUCED_MOTION) Chart.defaults.animation = false;
}

// ── Print: Chart.js draws directly to canvas pixels, so the print CSS's
// light-theme color overrides (see dashboard.css's @media print block)
// have no effect on already-rendered chart text/gridlines -- without
// this, every chart would print with pale light-theme tick/legend text
// on the new white page, unreadable. Recolors every live chart instance
// in place (no re-fetch) right before printing, then reverts after.
function setChartsPrintMode(isPrint) {
    const textColor = isPrint ? '#333333' : '#9aab96';
    const gridColor = isPrint ? 'rgba(0,0,0,0.08)' : 'rgba(255,255,255,0.04)';
    Chart.defaults.color = isPrint ? '#333333' : '#9aab96';
    Object.values(window.__charts || {}).forEach((chart) => {
        if (chart.options.plugins && chart.options.plugins.legend && chart.options.plugins.legend.labels) {
            chart.options.plugins.legend.labels.color = textColor;
        }
        ['x', 'y'].forEach((axis) => {
            const scale = chart.options.scales && chart.options.scales[axis];
            if (scale) {
                scale.ticks = scale.ticks || {};
                scale.ticks.color = textColor;
                if (scale.grid) scale.grid.color = gridColor;
            }
        });
        chart.update('none');
    });
}
window.addEventListener('beforeprint', () => setChartsPrintMode(true));
window.addEventListener('afterprint', () => setChartsPrintMode(false));

// ── Number / currency formatting ────────────────────────────────────

function formatIndianGrouping(n) {
    const neg = n < 0;
    n = Math.round(Math.abs(n));
    let s = String(n);
    if (s.length <= 3) return (neg ? '-' : '') + s;
    const last3 = s.slice(-3);
    let rest = s.slice(0, -3);
    rest = rest.replace(/\B(?=(\d{2})+(?!\d)$)/g, ',');
    return (neg ? '-' : '') + rest + ',' + last3;
}
function formatRs(n) { return 'Rs ' + formatIndianGrouping(n || 0); }
function formatNum(n, decimals) { return decimals ? (n || 0).toFixed(decimals) : formatIndianGrouping(n || 0); }
function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function formatLocalDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    const bs = BsCalendar.adToBs(d);
    const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    return `${BsCalendar.formatBs(bs, false)}, ${time}`;
}
function formatBsDateOnly(isoOrDate) {
    if (!isoOrDate) return '—';
    const d = typeof isoOrDate === 'string' ? new Date(isoOrDate) : isoOrDate;
    return BsCalendar.formatBs(BsCalendar.adToBs(d));
}

// Nepal-local "today" (A.D.) without depending on the browser's own
// timezone -- shift by the fixed +5:45 offset, read fields with UTC
// getters (see static/js/dashboard.js's predecessor for the same trick).
function nepalDateParts(offsetDays) {
    const d = new Date(Date.now() + (5 * 60 + 45) * 60000 + (offsetDays || 0) * 86400000);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth(), day: d.getUTCDate() };
}
function toYmd(y, m, day) { return `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`; }
function nepalTodayAd() { const p = nepalDateParts(0); return BsCalendar.utcDate(p.y, p.m, p.day); }
function adDateToYmd(d) { return `${d.getFullYear ? d.getFullYear() : d.getUTCFullYear()}-${String((d.getMonth ? d.getMonth() : d.getUTCMonth()) + 1).padStart(2, '0')}-${String((d.getDate ? d.getDate() : d.getUTCDate())).padStart(2, '0')}`;
}

// ── Fetch helper ─────────────────────────────────────────────────────

class ReportFetchError extends Error {}

async function fetchJSON(url) {
    let res;
    try {
        res = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    } catch (e) {
        throw new ReportFetchError('Needs a connection — could not reach the server.');
    }
    if (!res.ok) {
        let msg = `Request failed (${res.status}).`;
        try { const body = await res.json(); if (body && body.message) msg = body.message; } catch (e) { /* noop */ }
        throw new ReportFetchError(msg);
    }
    return res.json();
}

// ── Global state ──────────────────────────────────────────────────────

const state = {
    section: 'overview', start: null, end: null, channel: 'all', granularity: 'day', compare: false,
    payment_type: '', customer_type: '', operator: '',
};
let filtersSignature = '';
const loadedSections = {};
let bsGridYear = null;

function buildParams(extra) {
    const p = new URLSearchParams({
        start: state.start, end: state.end, channel: state.channel,
        granularity: state.granularity, compare: state.compare ? 'true' : 'false',
    });
    if (state.payment_type) p.set('payment_type', state.payment_type);
    if (state.customer_type) p.set('customer_type', state.customer_type);
    if (state.operator) p.set('operator', state.operator);
    if (extra) Object.keys(extra).forEach((k) => p.set(k, extra[k]));
    return p.toString();
}

// ── Date range presets (B.S.-aware) ────────────────────────────────────

function setRange(startDate, endDate) {
    state.start = adDateToYmd(startDate);
    state.end = adDateToYmd(endDate);
}

function applyPreset(preset) {
    document.getElementById('bsRangePicker').classList.toggle('active', preset === 'custom');
    if (preset === 'custom') { initCustomBsPicker(); return; }

    const today = nepalTodayAd();
    const todayBs = BsCalendar.adToBs(today);
    if (preset === 'today') setRange(today, today);
    else if (preset === 'yesterday') { const y = new Date(today.getTime() - 86400000); setRange(y, y); }
    else if (preset === 'last7') setRange(new Date(today.getTime() - 6 * 86400000), today);
    else if (preset === 'last30') setRange(new Date(today.getTime() - 29 * 86400000), today);
    else if (preset === 'thismonth') setRange(BsCalendar.bsMonthStartAd(todayBs.year, todayBs.month), today);
    else if (preset === 'lastmonth') {
        const prev = BsCalendar.bsAddMonths(todayBs.year, todayBs.month, -1);
        setRange(BsCalendar.bsMonthStartAd(prev.year, prev.month), BsCalendar.bsMonthEndAd(prev.year, prev.month));
    } else if (preset === 'thisbsyear') {
        setRange(BsCalendar.bsMonthStartAd(todayBs.year, 1), today);
    } else if (preset === 'fiscalyear') {
        setRange(BsCalendar.bsFiscalYearStart(today), today);
    }
    highlightBsGridForCurrentRange();
    loadAll();
}

// ── BS month grid (sidebar) ─────────────────────────────────────────────

function renderBsGrid() {
    if (bsGridYear == null) bsGridYear = BsCalendar.adToBs(nepalTodayAd()).year;
    document.getElementById('bsYearLabel').textContent = bsGridYear;
    const grid = document.getElementById('bsMonthGrid');
    const shortNames = BsCalendar.MONTH_NAMES;
    grid.innerHTML = shortNames.map((name, i) => `<button type="button" class="dash-bsgrid-month" data-month="${i + 1}" title="${name} ${bsGridYear}">${name}</button>`).join('');
    grid.querySelectorAll('.dash-bsgrid-month').forEach((btn) => {
        btn.addEventListener('click', () => {
            const month = parseInt(btn.dataset.month, 10);
            let start, end;
            try {
                start = BsCalendar.bsMonthStartAd(bsGridYear, month);
                end = BsCalendar.bsMonthEndAd(bsGridYear, month);
            } catch (e) { showConnError('That B.S. month is outside the supported range.'); return; }
            const today = nepalTodayAd();
            if (end > today) end = today;
            setRange(start, end);
            document.getElementById('presetSelect').value = 'custom';
            document.getElementById('bsRangePicker').classList.remove('active');
            highlightBsGridForCurrentRange();
            loadAll();
        });
    });
    highlightBsGridForCurrentRange();
}

function highlightBsGridForCurrentRange() {
    if (!state.start) return;
    const startBs = BsCalendar.adToBs(new Date(state.start + 'T00:00:00Z'));
    document.querySelectorAll('.dash-bsgrid-month').forEach((btn) => {
        btn.classList.toggle('active', startBs.year === bsGridYear && parseInt(btn.dataset.month, 10) === startBs.month);
    });
}

function initBsGridNav() {
    document.getElementById('bsYearPrev').addEventListener('click', () => { bsGridYear -= 1; renderBsGrid(); });
    document.getElementById('bsYearNext').addEventListener('click', () => { bsGridYear += 1; renderBsGrid(); });
}

// ── Custom B.S. range picker (3 selects x 2) ───────────────────────────

function initCustomBsPicker() {
    const todayBs = BsCalendar.adToBs(nepalTodayAd());
    const years = [];
    for (let y = BsCalendar.BS_EPOCH_YEAR; y <= BsCalendar.MAX_BS_YEAR; y++) years.push(y);

    function fillSelects(prefix, bs) {
        const daySel = document.getElementById(prefix + 'Day');
        const monthSel = document.getElementById(prefix + 'Month');
        const yearSel = document.getElementById(prefix + 'Year');
        monthSel.innerHTML = BsCalendar.MONTH_NAMES.map((n, i) => `<option value="${i + 1}">${n}</option>`).join('');
        yearSel.innerHTML = years.map((y) => `<option value="${y}">${y}</option>`).join('');
        monthSel.value = bs.month; yearSel.value = bs.year;
        const refreshDays = () => {
            const y = parseInt(yearSel.value, 10), m = parseInt(monthSel.value, 10);
            const len = BsCalendar.bsMonthLength(y, m);
            const current = parseInt(daySel.value, 10) || bs.day;
            daySel.innerHTML = Array.from({ length: len }, (_, i) => i + 1).map((d) => `<option value="${d}">${d}</option>`).join('');
            daySel.value = Math.min(current, len);
        };
        monthSel.addEventListener('change', refreshDays);
        yearSel.addEventListener('change', refreshDays);
        refreshDays();
    }
    fillSelects('bsStart', state.start ? BsCalendar.adToBs(new Date(state.start + 'T00:00:00Z')) : todayBs);
    fillSelects('bsEnd', state.end ? BsCalendar.adToBs(new Date(state.end + 'T00:00:00Z')) : todayBs);
}

function applyCustomBsRange() {
    try {
        const start = BsCalendar.bsToAd(
            parseInt(document.getElementById('bsStartYear').value, 10),
            parseInt(document.getElementById('bsStartMonth').value, 10),
            parseInt(document.getElementById('bsStartDay').value, 10),
        );
        const end = BsCalendar.bsToAd(
            parseInt(document.getElementById('bsEndYear').value, 10),
            parseInt(document.getElementById('bsEndMonth').value, 10),
            parseInt(document.getElementById('bsEndDay').value, 10),
        );
        setRange(start, end);
        highlightBsGridForCurrentRange();
        loadAll();
    } catch (e) {
        document.getElementById('filterError').textContent = 'Invalid B.S. date.';
    }
}

// ── Filters-active indicator ─────────────────────────────────────────

function updateFiltersActiveIndicator() {
    const parts = [];
    if (state.payment_type) parts.push('payment: ' + state.payment_type);
    if (state.customer_type) parts.push('customer: ' + state.customer_type);
    if (state.operator) {
        const sel = document.getElementById('operatorSelect');
        const label = sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].textContent : state.operator;
        parts.push('operator: ' + label);
    }
    const el = document.getElementById('filtersActiveIndicator');
    el.classList.toggle('active', parts.length > 0);
    document.getElementById('filtersActiveText').textContent = parts.length ? `Filters active — ${parts.join(', ')}` : '';
}

// ── Validation + load orchestration ────────────────────────────────────

function validateFilters() {
    const errEl = document.getElementById('filterError');
    errEl.textContent = '';
    if (!state.start || !state.end) { errEl.textContent = 'Please choose a date range.'; return false; }
    if (state.start > state.end) { errEl.textContent = "'Start' must not be after 'end'."; return false; }
    const days = (new Date(state.end) - new Date(state.start)) / 86400000 + 1;
    if (days > 366) { errEl.textContent = 'Date range cannot exceed 366 days.'; return false; }
    return true;
}

function loadAll() {
    if (!validateFilters()) return;
    updateFiltersActiveIndicator();
    const sig = JSON.stringify(state);
    if (sig !== filtersSignature) {
        filtersSignature = sig;
        Object.keys(loadedSections).forEach((k) => delete loadedSections[k]);
    }
    document.getElementById('dashConnError').style.display = 'none';
    const startBs = BsCalendar.adToBs(new Date(state.start + 'T00:00:00Z'));
    const endBs = BsCalendar.adToBs(new Date(state.end + 'T00:00:00Z'));
    document.getElementById('dashPrintRange').textContent =
        `${BsCalendar.formatBs(startBs)} to ${BsCalendar.formatBs(endBs)} (${state.channel}, ${state.section} section)`;
    loadSection(state.section, true);
    loadTargetCard();
}

function setLastUpdated() {
    const now = new Date();
    document.getElementById('lastUpdated').textContent = 'Updated ' + now.toLocaleTimeString('en-GB');
}

function showConnError(message) {
    const el = document.getElementById('dashConnError');
    el.style.display = 'block';
    el.innerHTML = `${escapeHtml(message)} <button type="button" onclick="loadAll()">Retry</button>`;
}

// ── Sections / hash routing ─────────────────────────────────────────────

const SECTION_TITLES = {
    overview: 'Overview', sales: 'Sales', products: 'Products', credit: 'Credit (उधारो)',
    inventory: 'Inventory', orders: 'Online Orders', alerts: 'Alerts', pl: 'Profit & Loss',
};
const SECTION_LOADERS = {
    overview: loadOverview, sales: loadSales, products: loadProducts,
    credit: loadCredit, inventory: loadInventory, orders: loadOrders, alerts: loadAlertsSection,
    pl: loadPl,
};

function loadSection(section, force) {
    if (!force && loadedSections[section]) return;
    loadedSections[section] = true;
    const loader = SECTION_LOADERS[section];
    if (loader) loader().then(setLastUpdated).catch((e) => showConnError(e.message));
}

function switchSection(section, pushHash) {
    state.section = section;
    document.getElementById('sectionTitle').textContent = SECTION_TITLES[section] || section;
    document.querySelectorAll('.dash-nav-item[role="tab"]').forEach((b) => {
        const active = b.dataset.section === section;
        b.setAttribute('aria-current', active ? 'page' : 'false');
    });
    document.querySelectorAll('.dash-section').forEach((s) => s.classList.toggle('active', s.id === `section-${section}`));
    if (pushHash !== false) location.hash = section;
    closeMobileSidebar();
    loadSection(section, false);
}

function initNav() {
    const tabs = Array.from(document.querySelectorAll('.dash-nav-item[role="tab"]'));
    tabs.forEach((btn, i) => {
        btn.addEventListener('click', () => switchSection(btn.dataset.section));
        // Standard ARIA tablist keyboard pattern -- up/down since this is a
        // vertical sidebar, not left/right (that'd fight the slide-out
        // panel's own left/right motion on mobile).
        btn.addEventListener('keydown', (ev) => {
            if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
            ev.preventDefault();
            const next = tabs[(i + (ev.key === 'ArrowDown' ? 1 : -1) + tabs.length) % tabs.length];
            next.focus();
            switchSection(next.dataset.section);
        });
    });
    window.addEventListener('hashchange', () => {
        const section = location.hash.replace('#', '') || 'overview';
        if (SECTION_TITLES[section]) switchSection(section, false);
    });
    const initial = location.hash.replace('#', '');
    if (SECTION_TITLES[initial]) switchSection(initial, false);
}

function closeMobileSidebar() {
    document.getElementById('dashSidebar').classList.remove('open');
    document.getElementById('sidebarBackdrop').classList.remove('open');
    document.getElementById('sidebarToggle').setAttribute('aria-expanded', 'false');
}

// ── Generic render helpers ─────────────────────────────────────────────

function skeletonHtml(count, height) {
    return Array.from({ length: count || 1 }).map(() => `<div class="dash-skeleton" style="height:${height || 50}px;margin-bottom:8px;"></div>`).join('');
}
function emptyStateHtml(message) { return `<div class="dash-empty-state">${escapeHtml(message || 'Nothing here for this date range.')}</div>`; }
function errorStateHtml(message, retryFnName) { return `<div class="dash-error-state">${escapeHtml(message)}<br><button type="button" onclick="${retryFnName}()">Retry</button></div>`; }

function kpiCard(label, info, formatter, sparkline) {
    formatter = formatter || formatRs;
    const dir = info.direction;
    let deltaHtml = '';
    if (dir != null) {
        const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : dir === 'new' ? '●' : '―';
        const dirText = dir === 'up' ? 'up' : dir === 'down' ? 'down' : dir === 'new' ? 'new' : 'flat';
        const deltaText = info.delta_pct == null
            ? (dir === 'new' ? 'new' : 'no change')
            : `${arrow} ${Math.abs(info.delta_pct)}%`;
        deltaHtml = `<div class="delta ${dir}">${escapeHtml(deltaText)} ${dir !== 'new' && dir !== 'flat' ? dirText : ''}</div>`;
    }
    const sparkId = 'spark_' + Math.random().toString(36).slice(2);
    const sparkHtml = sparkline ? `<canvas class="spark" id="${sparkId}" aria-hidden="true"></canvas>` : '';
    return { html: `<div class="dash-kpi-tile"><div class="label">${escapeHtml(label)}</div><div class="value" data-countup="${info.current}">${formatter(info.current)}</div>${deltaHtml}${sparkHtml}</div>`, sparkId: sparkline ? sparkId : null };
}

function doughnutLegendHtml(labels, values, formatter) {
    formatter = formatter || formatNum;
    const total = values.reduce((a, b) => a + b, 0) || 1;
    return `<ul style="list-style:none;padding:0;margin:8px 0 0;font-size:0.72rem;">${labels.map((l, i) => {
        const pct = ((values[i] / total) * 100).toFixed(1);
        return `<li style="display:flex;align-items:center;gap:6px;margin-bottom:3px;color:var(--dash-text-muted);"><span style="width:9px;height:9px;border-radius:2px;background:${PALETTE[i % PALETTE.length]};display:inline-block;flex-shrink:0;"></span>${escapeHtml(l)}: <strong style="color:var(--dash-text);">${formatter(values[i])}</strong> (${pct}%)</li>`;
    }).join('')}</ul>`;
}

function destroyChart(canvasId) { if (window.__charts && window.__charts[canvasId]) window.__charts[canvasId].destroy(); }

function makeChart(canvasId, config) {
    window.__charts = window.__charts || {};
    destroyChart(canvasId);
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    config.options = config.options || {};
    config.options.maintainAspectRatio = false;
    if (REDUCED_MOTION) config.options.animation = false;
    const chart = new Chart(canvas, config);
    window.__charts[canvasId] = chart;
    return chart;
}

// Zero-fills a daily {date, ...fields} series across [startIso, endIso]
// inclusive, so a sparse week doesn't draw a misleading slope between
// far-apart points. Only used for granularity='day' -- week/bs_month
// buckets are already whichever periods actually had rows.
function zeroFillDaily(rows, startIso, endIso, fields) {
    const byDate = {};
    rows.forEach((r) => { byDate[r.date] = r; });
    const out = [];
    let cur = new Date(startIso + 'T00:00:00Z');
    const end = new Date(endIso + 'T00:00:00Z');
    while (cur <= end) {
        const key = adDateToYmd(cur);
        if (byDate[key]) out.push(byDate[key]);
        else { const blank = { date: key }; fields.forEach((f) => { blank[f] = 0; }); out.push(blank); }
        cur = new Date(cur.getTime() + 86400000);
    }
    return out;
}

function bsLabelsForSeries(rows) { return rows.map((r) => formatBsDateOnly(r.date + 'T00:00:00Z')); }

function timeSeriesChartConfig(rows, datasetDefs, opts) {
    opts = opts || {};
    const useBars = rows.length < 4;
    const labels = bsLabelsForSeries(rows);
    const datasets = datasetDefs.map((d) => ({
        label: d.label, data: rows.map((r) => r[d.field] || 0),
        backgroundColor: useBars ? d.color : 'transparent', borderColor: d.color,
        borderDash: d.dashed ? [6, 4] : undefined, tension: 0, borderWidth: 2,
    }));
    return {
        type: useBars ? 'bar' : 'line',
        data: { labels, datasets },
        options: {
            plugins: { legend: { display: datasetDefs.length > 1, labels: { color: '#9aab96', boxWidth: 10, font: { size: 10 } } } },
            scales: {
                x: { ticks: { color: '#8fa391', font: { size: 9 } }, grid: { color: 'rgba(255,255,255,0.04)' } },
                y: {
                    ticks: {
                        color: '#8fa391', font: { size: 9 },
                        ...(opts.integerTicks ? { precision: 0, callback: (v) => Number.isInteger(v) ? v : null } : {}),
                    },
                    grid: { color: 'rgba(255,255,255,0.04)' },
                },
            },
        },
    };
}

// ── Sortable table helper ────────────────────────────────────────────

function renderTable(containerId, columns, rows, opts) {
    opts = opts || {};
    const el = document.getElementById(containerId);
    if (!rows.length) { el.innerHTML = emptyStateHtml(opts.emptyMessage); return; }
    let sortCol = opts.defaultSort != null ? opts.defaultSort : null;
    let sortDir = -1;
    function draw() {
        let sortedRows = rows.slice();
        if (sortCol != null) {
            sortedRows.sort((a, b) => {
                const av = a[columns[sortCol].key], bv = b[columns[sortCol].key];
                if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * sortDir;
                return String(av).localeCompare(String(bv)) * sortDir;
            });
        }
        const thead = `<tr>${columns.map((c, i) => `<th class="${c.num ? 'num' : ''}" data-col="${i}" tabindex="0" role="button" aria-label="Sort by ${escapeHtml(c.label)}">${escapeHtml(c.label)}${sortCol === i ? `<span class="sort-arrow">${sortDir === 1 ? '▲' : '▼'}</span>` : ''}</th>`).join('')}</tr>`;
        const tbody = sortedRows.map((r) => `<tr>${columns.map((c) => `<td class="${c.num ? 'num' : ''}">${c.render ? c.render(r) : escapeHtml(r[c.key])}</td>`).join('')}</tr>`).join('');
        el.innerHTML = `<table class="dash-table"><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
        el.querySelectorAll('th').forEach((th) => {
            const activate = () => {
                const col = parseInt(th.dataset.col, 10);
                if (sortCol === col) sortDir *= -1; else { sortCol = col; sortDir = 1; }
                draw();
            };
            th.addEventListener('click', activate);
            th.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); } });
        });
    }
    draw();
}

function deltaCellHtml(info) {
    if (!info || info.direction == null) return '—';
    const cls = info.direction === 'up' ? 'dash-delta-pos' : info.direction === 'down' ? 'dash-delta-neg' : '';
    const arrow = info.direction === 'up' ? '▲' : info.direction === 'down' ? '▼' : info.direction === 'new' ? '●' : '―';
    const text = info.delta_pct == null ? (info.direction === 'new' ? 'new' : '–') : `${Math.abs(info.delta_pct)}%`;
    return `<span class="${cls}">${arrow} ${text}</span>`;
}

// ── Target card ──────────────────────────────────────────────────────

async function loadTargetCard() {
    const el = document.getElementById('targetCard');
    try {
        const data = await fetchJSON('/api/v1/reports/target/');
        if (!data.has_target) {
            el.innerHTML = `<div class="dash-target-empty">No target set.<br><a href="/admin/shop/revenuetarget/add/" target="_blank" rel="noopener">Add one in admin &rarr;</a></div>`;
            return;
        }
        const startBs = formatBsDateOnly(data.start_date + 'T00:00:00Z');
        const endBs = formatBsDateOnly(data.end_date + 'T00:00:00Z');
        const pct = Math.min(100, data.pct_achieved);
        el.innerHTML = `
            <h4>${escapeHtml(data.name)}</h4>
            <div class="dash-target-amount">${formatRs(data.amount)}</div>
            <div class="dash-target-bar"><div class="dash-target-bar-fill" style="width:${pct}%;"></div></div>
            <div class="dash-target-meta">
                <strong>${data.pct_achieved}%</strong> achieved (POS sales) · ${formatRs(data.achieved)}<br>
                Remaining: <strong>${formatRs(data.remaining)}</strong><br>
                ${data.not_started ? `Starts ${startBs}` : `${data.days_left} day(s) left (to ${endBs})`}<br>
                ${data.days_left > 0 && data.remaining > 0 ? `Needed/day: <strong>${formatRs(data.needed_per_day)}</strong><br>` : ''}
                ${data.projected_total != null ? `Projected: <strong>${formatRs(data.projected_total)}</strong>` : ''}
                ${data.is_reached ? '<br><strong style="color:var(--dash-ok);">Target reached 🎯</strong>' : ''}
            </div>`;
    } catch (e) {
        el.innerHTML = errorStateHtml(e.message, 'loadTargetCard');
    }
}

// ── Overview ─────────────────────────────────────────────────────────

async function loadOverview() {
    document.getElementById('ovKpiStrip').innerHTML = skeletonHtml(1, 80);
    const data = await fetchJSON(`/api/v1/reports/summary/?${buildParams()}`);
    window.__lastSummary = data;
    renderHelpPanel();
    renderOverview(data);
}

function renderHelpPanel() {
    document.getElementById('helpPanelBody').innerHTML = `
        <ul>
            <li><strong>Sales (revenue)</strong> is POS sales only, after discounts. Website orders have no stored price, so online orders are a <strong>count</strong>, never a Rs figure.</li>
            <li><strong>Cash collected</strong> = non-credit POS payments + credit repayments received in the period. Not the same as revenue — a credit sale is revenue immediately, cash only once repaid.</li>
            <li><strong>Outstanding credit</strong> is a live, all-time snapshot — not scoped to the filter range.</li>
            <li><strong>Per-product/category revenue is an estimate ("est.")</strong>, priced at today's rates — individual lines don't store historical prices. Quantity/weight is exact.</li>
            <li><strong>Offer discounts aren't tracked</strong> as a Rs figure; <strong>coupon discounts are exact</strong>.</li>
            <li>No profit/margin anywhere — products have no recorded cost price (see the sidebar's disabled Profit &amp; Loss item).</li>
            <li><strong>Payment type / customer type / operator filters</strong> apply to Overview, Sales, and Products. They do NOT apply to Credit, Inventory, or Online Orders (each is already its own slice, or has no such concept) — those sections say so if a filter is active.</li>
            <li><strong>Projection</strong> extrapolates the current daily pace to the end of the selected period; hidden with fewer than 7 days of sales in the period.</li>
            <li>All dates are Bikram Sambat, Nepal local time. Dismissed/failed offline POS sales are Telegram-alerted only, never saved — this dashboard reflects synced sales.</li>
        </ul>`;
}

function renderOverview(data) {
    const k = data.kpis;
    document.getElementById('ovKpiStrip').innerHTML = [
        kpiCard('Total sales (POS)', k.total_sales).html,
        kpiCard('Transactions', k.transactions, (v) => formatNum(v)).html,
        kpiCard('AOV', k.aov).html,
        kpiCard('Cash collected', k.cash_collected).html,
        kpiCard('Credit given', k.credit_given).html,
        kpiCard('Repayments', k.repayments).html,
        kpiCard('Outstanding credit', k.outstanding_credit).html,
        kpiCard('Units / weight sold', { current: k.units_count.current, previous: null, delta_pct: null, direction: null },
            () => `${formatNum(k.units_count.current)} / ${formatNum(k.weight_kg.current, 2)}kg`).html,
    ].join('');

    renderTrendWithPeak(data.sales_over_time);
    renderPaymentMixOverview(data.payment_mix);
    renderTopProductsOverview(data.top_products);
    renderOverviewMiniBars(data.by_hour, data.by_day_of_week);
    renderCategoryOverview(data.category_mix);
    renderStaffRanking(data.staff_ranking);
    renderAlertsPreview(data.alerts);
    renderRunRate(data.run_rate);
    renderInsightStrip(data.insight_strip);
    renderProductComparison(data.product_comparison);
}

function renderTrendWithPeak(series) {
    let rows = series.current || [];
    if (state.granularity === 'day') rows = zeroFillDaily(rows, state.start, state.end, ['total', 'count']);
    if (!rows.length) {
        destroyChart('ovTrendChart');
        document.getElementById('ovTrendChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
        document.getElementById('ovTrendPeak').textContent = '';
        return;
    }
    const datasetDefs = [{ label: 'Sales', field: 'total', color: PALETTE[0] }];
    let previousRows = [];
    if (series.previous && series.previous.length) {
        previousRows = state.granularity === 'day' ? zeroFillDaily(series.previous, state.start, state.end, ['total', 'count']) : series.previous;
    }
    const config = timeSeriesChartConfig(rows, datasetDefs);
    if (previousRows.length) {
        config.data.datasets.push({ label: 'Previous period', data: previousRows.map((r) => r.total || 0), borderColor: PALETTE[1], borderDash: [6, 4], backgroundColor: 'transparent', borderWidth: 2, tension: 0 });
    }
    makeChart('ovTrendChart', config);

    const peak = rows.reduce((best, r) => (r.total > (best ? best.total : -1) ? r : best), null);
    document.getElementById('ovTrendPeak').textContent = peak && peak.total > 0 ? `Peak ${formatRs(peak.total)}, ${formatBsDateOnly(peak.date + 'T00:00:00Z')}` : '';

    document.querySelector('[data-chart="ovTrendChart"]').onclick = () => {
        const tbl = document.getElementById('ovTrendTable');
        const showing = tbl.style.display !== 'none';
        tbl.style.display = showing ? 'none' : 'block';
        if (!showing) renderTable('ovTrendTable', [{ key: 'date', label: 'Date', render: (r) => formatBsDateOnly(r.date + 'T00:00:00Z') }, { key: 'total', label: 'Sales', num: true, render: (r) => formatRs(r.total) }], rows);
    };
}

function renderPaymentMixOverview(mix) {
    const wrap = document.getElementById('ovPaymentMixChart').closest('.dash-card');
    if (!mix.length) {
        destroyChart('ovPaymentMixChart');
        document.getElementById('ovPaymentMixChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
        document.getElementById('ovPaymentMixLegend').innerHTML = '';
        return;
    }
    makeChart('ovPaymentMixChart', { type: 'doughnut', data: { labels: mix.map((p) => p.method), datasets: [{ data: mix.map((p) => p.total), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
    document.getElementById('ovPaymentMixLegend').innerHTML = doughnutLegendHtml(mix.map((p) => p.method), mix.map((p) => p.total), formatRs);
}

function renderTopProductsOverview(products) {
    if (!products.length) { destroyChart('ovTopProductsChart'); document.getElementById('ovTopProductsChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); return; }
    makeChart('ovTopProductsChart', { type: 'bar', data: { labels: products.map((p) => p.name), datasets: [{ label: 'Qty', data: products.map((p) => p.qty + p.weight_kg), backgroundColor: PALETTE[0] }] }, options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#8fa391', font: { size: 9 } } }, y: { ticks: { color: '#8fa391', font: { size: 9 } } } } } });
}

function renderOverviewMiniBars(byHour, byDow) {
    makeChart('ovByHourChart', {
        type: 'bar',
        data: { labels: Array.from({ length: 24 }, (_, h) => h), datasets: [{ data: byHour, backgroundColor: PALETTE[1] }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { precision: 0, color: '#8fa391', font: { size: 9 } } }, x: { ticks: { color: '#8fa391', font: { size: 8 } } } } },
    });
    makeChart('ovByDowChart', {
        type: 'bar',
        data: { labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], datasets: [{ data: byDow, backgroundColor: PALETTE[2] }] },
        options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { precision: 0, color: '#8fa391', font: { size: 9 } } }, x: { ticks: { color: '#8fa391', font: { size: 9 } } } } },
    });
}

function renderCategoryOverview(categories) {
    if (!categories.length) { destroyChart('ovCategoryChart'); document.getElementById('ovCategoryChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); document.getElementById('ovCategoryLegend').innerHTML = ''; return; }
    makeChart('ovCategoryChart', { type: 'doughnut', data: { labels: categories.map((c) => c.category), datasets: [{ data: categories.map((c) => c.revenue_est), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
    document.getElementById('ovCategoryLegend').innerHTML = doughnutLegendHtml(categories.map((c) => c.category), categories.map((c) => c.revenue_est), formatRs);
}

function renderStaffRanking(ranking) {
    const el = document.getElementById('ovStaffRanking');
    if (!ranking.length) { el.innerHTML = emptyStateHtml('No sales in this period.'); return; }
    const max = Math.max(...ranking.map((r) => r.total));
    el.innerHTML = ranking.map((r, i) => `
        <div class="dash-rank-row">
            <div class="dash-rank-badge ${i === 0 ? 'gold' : ''}">${i + 1}</div>
            <div class="dash-rank-info">
                <div class="dash-rank-name">${escapeHtml(r.operator)}</div>
                <div class="dash-rank-bar-wrap"><div class="dash-rank-bar" style="width:${max ? (r.total / max * 100) : 0}%;"></div></div>
            </div>
            <div class="dash-rank-amount">${formatRs(r.total)}</div>
        </div>`).join('');
}

function renderAlertsPreview(alerts) {
    const el = document.getElementById('ovAlertsPreview');
    const items = [
        alerts.low_stock_count > 0 ? `${alerts.low_stock_count} low-stock product(s)` : null,
        alerts.no_sales_count > 0 ? `${alerts.no_sales_count} product(s) with no recent sales` : null,
        alerts.pending_online_orders > 0 ? `${alerts.pending_online_orders} pending online order(s)` : null,
        alerts.overdue_credit_total > 0 ? `${formatRs(alerts.overdue_credit_total)} outstanding credit` : null,
    ].filter(Boolean);
    const countEl = document.getElementById('ovAlertsCount');
    const navBadge = document.getElementById('alertsNavCount');
    if (items.length) {
        countEl.textContent = `(${items.length})`;
        navBadge.textContent = items.length; navBadge.style.display = 'inline-block';
        el.innerHTML = items.map((t) => `<div class="dash-alert-row"><svg class="dash-alert-icon warn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4"/><path d="M12 17h.01"/><circle cx="12" cy="12" r="10"/></svg>${escapeHtml(t)}</div>`).join('') +
            `<div style="margin-top:8px;"><button type="button" class="dash-icon-btn" onclick="switchSection('alerts')" style="font-size:0.72rem;">View all alerts &rarr;</button></div>`;
    } else {
        countEl.textContent = '';
        navBadge.style.display = 'none';
        el.innerHTML = `<div class="dash-alert-clear"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg><br>All clear</div>`;
    }
}

function renderRunRate(rr) {
    const el = document.getElementById('ovRunRate');
    if (!rr || !rr.available) { el.innerHTML = emptyStateHtml(rr ? rr.reason : 'Not enough data.'); return; }
    el.innerHTML = `
        <div class="dash-insight-card" style="text-align:left;">
            <div class="label">Projected total (full period)</div>
            <div class="value" style="font-size:1.3rem;">${formatRs(rr.projected_total)}</div>
        </div>
        <div style="font-size:0.74rem;color:var(--dash-text-muted);margin-top:8px;line-height:1.6;">
            Daily average so far: <strong style="color:var(--dash-text);">${formatRs(rr.daily_avg)}</strong><br>
            Based on ${rr.elapsed_days} of ${rr.full_period_days} day(s) elapsed.
        </div>`;
}

function renderInsightStrip(insight) {
    const el = document.getElementById('ovInsightStrip');
    if (!insight) { el.innerHTML = emptyStateHtml(); return; }
    el.innerHTML = `
        <div class="dash-insight-card"><div class="label">Best day</div><div class="value">${insight.best_day ? formatRs(insight.best_day.total) : '—'}</div><div style="font-size:0.65rem;color:var(--dash-text-dim);">${insight.best_day ? formatBsDateOnly(insight.best_day.date + 'T00:00:00Z') : 'no sales'}</div></div>
        <div class="dash-insight-card"><div class="label">Busiest hour</div><div class="value">${insight.peak_hour != null ? insight.peak_hour + ':00' : '—'}</div></div>
        <div class="dash-insight-card"><div class="label">Avg daily sales</div><div class="value">${formatRs(insight.avg_daily_sales)}</div></div>
        <div class="dash-insight-card"><div class="label">Top product</div><div class="value" style="font-size:0.82rem;">${insight.top_product ? escapeHtml(insight.top_product.name) : '—'}</div></div>
        <div class="dash-insight-card"><div class="label">Days with sales</div><div class="value">${insight.days_with_sales}</div></div>`;
}

function renderProductComparison(rows) {
    if (!rows.length) { document.getElementById('ovProductCompareTable').innerHTML = emptyStateHtml('No sales in this period.'); return; }
    renderTable('ovProductCompareTable', [
        { key: 'name', label: 'Product' },
        { key: 'qty', label: 'Qty now', num: true, render: (r) => formatNum(r.qty + r.weight_kg, r.weight_kg ? 2 : 0) },
        { key: 'qty_previous', label: 'Qty before', num: true, render: (r) => formatNum(r.qty_previous + r.weight_kg_previous, r.weight_kg_previous ? 2 : 0) },
        { key: 'change', label: 'Change', num: true, render: (r) => deltaCellHtml(r.qty_change) },
        { key: 'share_pct', label: 'Share', num: true, render: (r) => r.share_pct + '%' },
        { key: 'revenue_est', label: 'Revenue (est.)', num: true, render: (r) => formatRs(r.revenue_est) },
    ], rows);
}

// ── Sales section ────────────────────────────────────────────────────

let salesPage = 1;

async function loadSales() {
    document.getElementById('recentSalesTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportSalesBtn').href = `/api/v1/reports/sales-trend/?${buildParams({ export: 'csv' })}`;
    salesPage = 1;
    const data = await fetchJSON(`/api/v1/reports/sales-trend/?${buildParams()}`);
    renderSalesTrend(data.trend);
    renderSalesTable(data.recent_sales);
    renderTable('byOperatorTable', [
        { key: 'operator', label: 'Operator' }, { key: 'total', label: 'Total', num: true, render: (r) => formatRs(r.total) }, { key: 'count', label: 'Sales', num: true },
    ], data.by_operator, { emptyMessage: 'No sales in this period.' });
}

function renderSalesTrend(trend) {
    let rows = trend.series.current || [];
    if (state.granularity === 'day') rows = zeroFillDaily(rows, state.start, state.end, ['total', 'count']);
    if (rows.length) {
        const config = timeSeriesChartConfig(rows, [{ label: 'Revenue', field: 'total', color: PALETTE[0] }]);
        if (trend.series.previous && trend.series.previous.length) {
            let prevRows = state.granularity === 'day' ? zeroFillDaily(trend.series.previous, state.start, state.end, ['total']) : trend.series.previous;
            config.data.datasets.push({ label: 'Previous period', data: prevRows.map((r) => r.total || 0), borderColor: PALETTE[1], borderDash: [6, 4], backgroundColor: 'transparent', borderWidth: 2, tension: 0 });
        }
        makeChart('salesTrendChart', config);
    } else {
        destroyChart('salesTrendChart');
        document.getElementById('salesTrendChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }
    makeChart('salesByHourChart', { type: 'bar', data: { labels: Array.from({ length: 24 }, (_, h) => h), datasets: [{ label: 'Sales', data: trend.by_hour, backgroundColor: PALETTE[1] }] }, options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { precision: 0, color: '#8fa391' } }, x: { ticks: { color: '#8fa391', font: { size: 9 } } } } } });
    makeChart('salesByDowChart', { type: 'bar', data: { labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], datasets: [{ label: 'Sales', data: trend.by_day_of_week, backgroundColor: PALETTE[2] }] }, options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { precision: 0, color: '#8fa391' } }, x: { ticks: { color: '#8fa391' } } } } });
}

function renderSalesTable(page) {
    const el = document.getElementById('recentSalesTable');
    if (!page.results.length) { el.innerHTML = emptyStateHtml('No sales in this period.'); document.getElementById('recentSalesPager').innerHTML = ''; return; }
    el.innerHTML = `<table class="dash-table"><thead><tr><th>Sale #</th><th>Time</th><th>Customer</th><th>Operator</th><th class="num">Total</th><th>Payment</th></tr></thead><tbody>${
        page.results.map((r) => `<tr><td>${escapeHtml(r.sale_number)}</td><td>${formatLocalDateTime(r.created_at)}</td><td>${escapeHtml(r.customer || '—')}</td><td>${escapeHtml(r.operator)}</td><td class="num">${formatRs(r.total)}</td><td>${r.payments.map((p) => `${escapeHtml(p.method)}: ${formatRs(p.amount)}`).join(', ')}</td></tr>`).join('')
    }</tbody></table>`;
    const totalPages = Math.max(1, Math.ceil(page.count / page.page_size));
    document.getElementById('recentSalesPager').innerHTML = `
        <button type="button" ${salesPage <= 1 ? 'disabled' : ''} id="salesPrev">Prev</button>
        <span>Page ${salesPage} of ${totalPages}</span>
        <button type="button" ${salesPage >= totalPages ? 'disabled' : ''} id="salesNext">Next</button>`;
    const prevBtn = document.getElementById('salesPrev'), nextBtn = document.getElementById('salesNext');
    if (prevBtn) prevBtn.onclick = () => { salesPage -= 1; fetchJSON(`/api/v1/reports/sales-trend/?${buildParams({ page: salesPage })}`).then((d) => renderSalesTable(d.recent_sales)); };
    if (nextBtn) nextBtn.onclick = () => { salesPage += 1; fetchJSON(`/api/v1/reports/sales-trend/?${buildParams({ page: salesPage })}`).then((d) => renderSalesTable(d.recent_sales)); };
}

// ── Products section ─────────────────────────────────────────────────

let topProductsBy = 'revenue';

async function loadProducts() {
    document.getElementById('productTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportProductsBtn').href = `/api/v1/reports/products/?${buildParams({ export: 'csv' })}`;
    const data = await fetchJSON(`/api/v1/reports/products/?${buildParams({ by: topProductsBy })}`);
    window.__productsData = data;
    renderTopProductsChart(data.top_products, topProductsBy);
    renderCategoryChart(data.category_breakdown);
    renderTable('slowMoversTable', [{ key: 'name', label: 'Product' }], data.slow_movers, { emptyMessage: 'Everything available sold at least once this period.' });
    renderTable('productTable', [
        { key: 'name', label: 'Product' },
        { key: 'qty', label: 'Qty', num: true },
        { key: 'weight_kg', label: 'Weight (kg)', num: true },
        { key: 'revenue_est', label: 'Revenue (est.)', num: true, render: (r) => formatRs(r.revenue_est) },
        { key: 'share_pct', label: 'Share %', num: true, render: (r) => r.share_pct + '%' },
    ], data.product_table, { emptyMessage: 'No sales in this period.' });
    renderOffersTable(data.offers_performance);
}

function refreshTopProductsChart() {
    fetchJSON(`/api/v1/reports/products/?${buildParams({ by: topProductsBy })}`).then((data) => renderTopProductsChart(data.top_products, topProductsBy)).catch((e) => showConnError(e.message));
}

function renderTopProductsChart(products, by) {
    if (!products.length) { destroyChart('productsTopChart'); document.getElementById('productsTopChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); return; }
    const values = products.map((p) => by === 'revenue' ? p.revenue_est : (p.qty + p.weight_kg));
    makeChart('productsTopChart', { type: 'bar', data: { labels: products.map((p) => p.name), datasets: [{ label: by === 'revenue' ? 'Revenue (est.)' : 'Qty', data: values, backgroundColor: PALETTE[0] }] }, options: { indexAxis: 'y', plugins: { legend: { display: false } }, scales: { x: { ticks: { color: '#8fa391' } }, y: { ticks: { color: '#8fa391' } } } } });
}

function renderCategoryChart(categories) {
    if (!categories.length) { destroyChart('categoryChart'); document.getElementById('categoryChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); document.getElementById('categoryLegend').innerHTML = ''; return; }
    makeChart('categoryChart', { type: 'doughnut', data: { labels: categories.map((c) => c.category), datasets: [{ data: categories.map((c) => c.revenue_est), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
    document.getElementById('categoryLegend').innerHTML = doughnutLegendHtml(categories.map((c) => c.category), categories.map((c) => c.revenue_est), formatRs);
}

function renderOffersTable(perf) {
    const el = document.getElementById('offersTable');
    const offersHtml = perf.offers.length
        ? `<h4 style="margin:10px 0 4px;font-size:0.8rem;color:var(--dash-text);">Offers</h4><table class="dash-table"><thead><tr><th>Offer</th><th class="num">Uses</th><th>Discount given</th></tr></thead><tbody>${perf.offers.map((o) => `<tr><td>${escapeHtml(o.title)}</td><td class="num">${o.uses}</td><td>not tracked</td></tr>`).join('')}</tbody></table>`
        : '';
    const couponsHtml = perf.coupons.length
        ? `<h4 style="margin:10px 0 4px;font-size:0.8rem;color:var(--dash-text);">Coupons</h4><table class="dash-table"><thead><tr><th>Code</th><th class="num">Uses</th><th class="num">Discount given</th></tr></thead><tbody>${perf.coupons.map((c) => `<tr><td>${escapeHtml(c.code)}</td><td class="num">${c.uses}</td><td class="num">${formatRs(c.discount_given)}</td></tr>`).join('')}</tbody></table>`
        : '';
    el.innerHTML = (offersHtml + couponsHtml) || emptyStateHtml('No offers or coupons used in this period.');
}

// ── Credit section ────────────────────────────────────────────────────

async function loadCredit() {
    document.getElementById('creditKpis').innerHTML = skeletonHtml(1, 70);
    document.getElementById('exportCreditBtn').href = `/api/v1/reports/credit/?${buildParams({ export: 'csv' })}`;
    const data = await fetchJSON(`/api/v1/reports/credit/?${buildParams()}`);
    const k = data.kpis;
    document.getElementById('creditKpis').innerHTML = [
        kpiCard('Credit given', k.given).html, kpiCard('Repaid', k.repaid).html, kpiCard('Outstanding (all-time)', k.outstanding).html,
        kpiCard('Customers with balance', k.customers_with_balance, (v) => formatNum(v)).html,
    ].join('');

    const buckets = data.ageing_buckets;
    makeChart('ageingChart', { type: 'bar', data: { labels: ['0-30 days', '31-60 days', '61-90 days', '90+ days'], datasets: [{ label: 'Customers', data: [buckets['0-30'], buckets['31-60'], buckets['61-90'], buckets['90+']], backgroundColor: [PALETTE[1], PALETTE[0], PALETTE[3], PALETTE[3]] }] }, options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { precision: 0, color: '#8fa391' } }, x: { ticks: { color: '#8fa391' } } } } });

    if (data.trend.length) {
        // Always grouped bars (never crossing lines), per the explicit fix
        // for the 1-2-data-point case -- and it reads just as well with more.
        const labels = bsLabelsForSeries(data.trend);
        makeChart('creditTrendChart', { type: 'bar', data: { labels, datasets: [
            { label: 'Given', data: data.trend.map((t) => t.given), backgroundColor: PALETTE[3] },
            { label: 'Repaid', data: data.trend.map((t) => t.repaid), backgroundColor: PALETTE[1] },
        ] }, options: { scales: { x: { ticks: { color: '#8fa391', font: { size: 9 } } }, y: { ticks: { color: '#8fa391' } } } } });
    } else {
        destroyChart('creditTrendChart');
        document.getElementById('creditTrendChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }

    renderTable('creditCustomersTable', [
        { key: 'name', label: 'Name' }, { key: 'phone', label: 'Phone' },
        { key: 'balance', label: 'Balance', num: true, render: (r) => formatRs(r.balance) },
        { key: 'last_purchase', label: 'Last purchase', render: (r) => formatLocalDateTime(r.last_purchase) },
        { key: 'last_repayment', label: 'Last repayment', render: (r) => formatLocalDateTime(r.last_repayment) },
        { key: 'age_bucket', label: 'Age bucket' },
    ], data.customers, { emptyMessage: 'No customers currently owe anything.', defaultSort: 2 });
}

// ── Inventory section ─────────────────────────────────────────────────

async function loadInventory() {
    document.getElementById('stockTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportInventoryBtn').href = `/api/v1/reports/inventory/?${buildParams({ export: 'csv' })}`;
    const data = await fetchJSON(`/api/v1/reports/inventory/?${buildParams()}`);
    if (data.movements_by_type.length) {
        const byUnit = {};
        data.movements_by_type.forEach((m) => { (byUnit[m.unit] = byUnit[m.unit] || []).push(m); });
        const allTypes = [...new Set(data.movements_by_type.map((m) => m.movement_type))];
        const datasets = Object.keys(byUnit).map((unit, i) => ({
            label: unit, backgroundColor: PALETTE[i % PALETTE.length],
            data: allTypes.map((t) => { const row = byUnit[unit].find((m) => m.movement_type === t); return row ? row.total : 0; }),
        }));
        makeChart('movementsChart', { type: 'bar', data: { labels: allTypes, datasets }, options: { scales: { x: { ticks: { color: '#8fa391', font: { size: 9 } } }, y: { ticks: { color: '#8fa391' } } } } });
    } else {
        destroyChart('movementsChart');
        document.getElementById('movementsChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }
    renderTable('wasteTable', [{ key: 'product', label: 'Product' }, { key: 'total', label: 'Qty wasted', num: true }, { key: 'count', label: 'Entries', num: true }], data.waste, { emptyMessage: 'No waste recorded in this period.' });
    renderTable('stockTable', [
        { key: 'name', label: 'Product' },
        { key: 'stock', label: 'Current stock', num: true, render: (r) => `${formatNum(r.stock, r.pricing_mode === 'variable_weight' ? 2 : 0)} ${escapeHtml(r.unit)}` },
        { key: 'low_stock', label: 'Status', render: (r) => r.low_stock ? '⚠ Low stock' : 'OK' },
    ], data.stock_table, { emptyMessage: 'No available products.' });
}

// ── Online Orders section ─────────────────────────────────────────────

let ordersPage = 1;
const ORDER_STATUS_FUNNEL_ORDER = ['pending', 'confirmed', 'delivered', 'cancelled'];

async function loadOrders() {
    document.getElementById('recentOrdersTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportOrdersBtn').href = `/api/v1/reports/orders/?${buildParams({ export: 'csv' })}`;
    ordersPage = 1;
    const data = await fetchJSON(`/api/v1/reports/orders/?${buildParams()}`);
    const statuses = Object.keys(data.by_status);
    if (statuses.length) {
        makeChart('ordersByStatusChart', { type: 'doughnut', data: { labels: statuses, datasets: [{ data: statuses.map((s) => data.by_status[s]), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
        document.getElementById('ordersByStatusLegend').innerHTML = doughnutLegendHtml(statuses, statuses.map((s) => data.by_status[s]), (v) => formatNum(v));
    } else {
        destroyChart('ordersByStatusChart');
        document.getElementById('ordersByStatusChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
        document.getElementById('ordersByStatusLegend').innerHTML = '';
    }
    renderOrdersFunnel(data.by_status);
    if (data.over_time.length) {
        const rows = state.granularity === 'day' ? zeroFillDaily(data.over_time, state.start, state.end, ['count']) : data.over_time;
        makeChart('ordersOverTimeChart', timeSeriesChartConfig(rows, [{ label: 'Orders', field: 'count', color: PALETTE[0] }], { integerTicks: true }));
    } else {
        destroyChart('ordersOverTimeChart');
        document.getElementById('ordersOverTimeChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }
    renderOrdersTable(data.recent_orders);
}

function renderOrdersFunnel(byStatus) {
    const el = document.getElementById('ordersFunnel');
    const total = Object.values(byStatus).reduce((a, b) => a + b, 0);
    const ordered = ORDER_STATUS_FUNNEL_ORDER.filter((s) => s in byStatus).concat(Object.keys(byStatus).filter((s) => !ORDER_STATUS_FUNNEL_ORDER.includes(s)));
    if (!total) { el.innerHTML = emptyStateHtml('No orders in this period.'); return; }
    el.innerHTML = ordered.map((s) => {
        const count = byStatus[s] || 0;
        const pct = total ? (count / total * 100) : 0;
        return `<div style="margin-bottom:8px;"><div style="display:flex;justify-content:space-between;font-size:0.76rem;color:var(--dash-text-muted);"><span>${escapeHtml(s)}</span><span>${count}</span></div><div style="height:8px;background:rgba(255,255,255,0.06);border-radius:999px;margin-top:3px;"><div style="height:100%;width:${pct}%;background:${s === 'cancelled' ? 'var(--dash-danger)' : 'var(--dash-moss)'};border-radius:999px;"></div></div></div>`;
    }).join('');
}

function renderOrdersTable(page) {
    const el = document.getElementById('recentOrdersTable');
    if (!page.results.length) { el.innerHTML = emptyStateHtml('No orders in this period.'); document.getElementById('recentOrdersPager').innerHTML = ''; return; }
    el.innerHTML = `<table class="dash-table"><thead><tr><th>Order #</th><th>Name</th><th>Status</th><th>Ordered</th><th>Admin</th></tr></thead><tbody>${
        page.results.map((r) => `<tr><td>${escapeHtml(r.order_number)}</td><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.status)}</td><td>${formatLocalDateTime(r.ordered_at)}</td><td><a href="/admin/shop/productorder/${r.id}/change/" target="_blank" rel="noopener" style="color:var(--dash-gold);">View</a></td></tr>`).join('')
    }</tbody></table>`;
    const totalPages = Math.max(1, Math.ceil(page.count / page.page_size));
    document.getElementById('recentOrdersPager').innerHTML = `
        <button type="button" ${ordersPage <= 1 ? 'disabled' : ''} id="ordersPrev">Prev</button>
        <span>Page ${ordersPage} of ${totalPages}</span>
        <button type="button" ${ordersPage >= totalPages ? 'disabled' : ''} id="ordersNext">Next</button>`;
    const prevBtn = document.getElementById('ordersPrev'), nextBtn = document.getElementById('ordersNext');
    if (prevBtn) prevBtn.onclick = () => { ordersPage -= 1; fetchJSON(`/api/v1/reports/orders/?${buildParams({ page: ordersPage })}`).then((d) => renderOrdersTable(d.recent_orders)); };
    if (nextBtn) nextBtn.onclick = () => { ordersPage += 1; fetchJSON(`/api/v1/reports/orders/?${buildParams({ page: ordersPage })}`).then((d) => renderOrdersTable(d.recent_orders)); };
}

// ── Alerts section (full detail) ───────────────────────────────────────

async function loadAlertsSection() {
    document.getElementById('alertsLowStock').innerHTML = skeletonHtml(2);
    const data = await fetchJSON('/api/v1/reports/alerts/');

    renderTable('alertsLowStock', [{ key: 'name', label: 'Product' }, { key: 'stock', label: 'Stock', num: true, render: (r) => `${formatNum(r.stock)} ${escapeHtml(r.unit || '')}` }], data.low_stock, { emptyMessage: 'No low-stock products.' });
    renderTable('alertsNoSales', [
        { key: 'name', label: 'Product' },
        { key: 'days_since_last_sale', label: 'Last sold', render: (r) => r.days_since_last_sale == null ? 'never (in lookback window)' : `${r.days_since_last_sale} day(s) ago` },
    ], data.no_sales, { emptyMessage: `Everything has sold within ${data.thresholds.no_sales_days} days.` });
    renderTable('alertsStaleOrders', [
        { key: 'order_number', label: 'Order #' }, { key: 'name', label: 'Customer' },
        { key: 'days_pending', label: 'Pending', num: true, render: (r) => `${r.days_pending} day(s)` },
    ], data.stale_orders, { emptyMessage: `No orders pending longer than ${data.thresholds.order_pending_days} days.` });
    renderTable('alertsHighCredit', [
        { key: 'name', label: 'Customer' }, { key: 'phone', label: 'Phone' },
        { key: 'balance', label: 'Balance', num: true, render: (r) => formatRs(r.balance) },
    ], data.high_credit_customers, { emptyMessage: `No balances above ${formatRs(data.thresholds.credit_balance)}.` });

    const milestoneCard = document.getElementById('alertsTargetMilestoneCard');
    if (data.target_milestone) {
        milestoneCard.style.display = 'block';
        document.getElementById('alertsTargetMilestone').innerHTML = `<div class="dash-alert-row"><svg class="dash-alert-icon info" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>"${escapeHtml(data.target_milestone.target_name)}" reached ${data.target_milestone.milestone_pct}% of target.</div>`;
    } else {
        milestoneCard.style.display = 'none';
    }

    const allClear = !data.low_stock.length && !data.no_sales.length && !data.stale_orders.length && !data.high_credit_customers.length && !data.target_milestone;
    if (allClear) {
        document.querySelectorAll('#section-alerts .dash-card').forEach((c) => { if (c.id !== 'alertsTargetMilestoneCard') c.style.display = 'none'; });
        document.querySelector('#section-alerts .dash-grid').insertAdjacentHTML('afterbegin', `<div class="dash-card c12"><div class="dash-alert-clear"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg><br>All clear — nothing needs attention.</div></div>`);
    }
}

// ── Profit & Loss section ───────────────────────────────────────────────
//
// Unlike every other tab, this one is POS-only (no channel/payment_type/
// customer_type/operator concept at all -- see shop/pl_report.py's module
// docstring) and the API takes from/to, not start/end. It still reuses
// the SAME global B.S.-aware date range the topbar already resolves
// (state.start/state.end), rather than building a second period picker,
// since the existing preset dropdown (this BS month / this BS year /
// fiscal year / custom) already covers every period shape this report
// needs.

function plParams(extra) {
    const p = new URLSearchParams({ from: state.start, to: state.end });
    if (extra) Object.keys(extra).forEach((k) => p.set(k, extra[k]));
    return p.toString();
}

function plStatementRow(label, value, opts) {
    opts = opts || {};
    const labelHtml = opts.strong ? `<strong>${escapeHtml(label)}</strong>` : escapeHtml(label);
    const valueHtml = opts.strong ? `<strong>${formatRs(value)}</strong>` : formatRs(value);
    return `<tr class="${opts.indent ? 'pl-indent' : ''}"><td>${labelHtml}</td><td class="num">${valueHtml}</td></tr>`;
}

const PL_DQ_LABELS = {
    website_orders_excluded: 'Website orders excluded (no stored price)',
    sales_without_line_data: 'Older POS sales with no line-item detail',
    sourced_lines_no_cost: 'Sourced sale lines missing a cost',
    waste_no_cost: 'Waste movements missing a cost',
    unmapped_cost_centre_entries: 'Farm cost entries with an unmapped cost centre',
    farm_products_no_mapping: 'Farm products never mapped to a cost centre',
    shared_allocation_fallback: 'Shared costs/depreciation that fell back to "unallocated"',
    dangling_reversals: 'Reversal entries with no matching original',
};

async function loadPl() {
    document.getElementById('plKpis').innerHTML = skeletonHtml(1, 70);
    document.getElementById('exportPlBtn').href = `/api/v1/reports/pl/?${plParams({ export: 'csv' })}`;
    const data = await fetchJSON(`/api/v1/reports/pl/?${plParams()}`);

    document.getElementById('plKpis').innerHTML = [
        ['Net profit', data.net_profit], ['Total revenue', data.revenue.total],
        ['Cost of sales', data.cost_of_sales], ['Wastage loss', data.wastage_loss],
        ['Farm costs', data.farm_costs_total.total], ['Depreciation', data.depreciation.total],
    ].map(([label, value]) => `<div class="dash-kpi-tile"><div class="label">${escapeHtml(label)}</div><div class="value">${formatRs(value)}</div></div>`).join('');

    document.getElementById('plStatementTable').innerHTML = `<table class="dash-table">
        <tbody>
            ${plStatementRow('Revenue -- retail', data.revenue.retail, { indent: true })}
            ${plStatementRow('Revenue -- wholesale', data.revenue.wholesale, { indent: true })}
            ${plStatementRow('Total revenue (POS only)', data.revenue.total, { strong: true })}
            ${plStatementRow('Less: cost of sales (sourced products)', data.cost_of_sales)}
            ${plStatementRow('Less: wastage loss', data.wastage_loss)}
            ${plStatementRow('Less: stock count adjustments', data.stock_adjustments)}
            ${plStatementRow('Gross profit on sourced products', data.gross_profit_sourced, { strong: true })}
            ${plStatementRow('Less: farm costs -- direct', data.farm_costs_total.direct, { indent: true })}
            ${plStatementRow('Less: farm costs -- shared, allocated', data.farm_costs_total.shared_allocated, { indent: true })}
            ${plStatementRow('Less: farm costs -- unallocated', data.farm_costs_total.unallocated_overhead, { indent: true })}
            ${plStatementRow('Less: depreciation', data.depreciation.total)}
            ${plStatementRow('Net profit', data.net_profit, { strong: true })}
        </tbody>
    </table>`;

    const centres = data.farm_cost_centres;
    renderTable('plCentreTable', [
        { key: 'cost_centre', label: 'Cost centre' },
        { key: 'revenue', label: 'Revenue', num: true, render: (r) => formatRs(r.revenue) },
        { key: 'direct_cost', label: 'Direct cost', num: true, render: (r) => formatRs(r.direct_cost) },
        { key: 'allocated_shared_cost', label: 'Shared cost', num: true, render: (r) => formatRs(r.allocated_shared_cost) },
        { key: 'depreciation', label: 'Depreciation', num: true, render: (r) => formatRs(r.depreciation) },
        { key: 'profit', label: 'Profit', num: true, render: (r) => formatRs(r.profit) },
        { key: 'margin_pct', label: 'Margin', num: true, render: (r) => r.margin_pct == null ? '—' : `${r.margin_pct}%` },
    ], centres, { emptyMessage: 'No cost-centre activity this period -- map products in Admin → Cost Centre Products.' });

    if (centres.length) {
        makeChart('plCentreChart', {
            type: 'bar',
            data: {
                labels: centres.map((c) => c.cost_centre),
                datasets: [
                    { label: 'Revenue', data: centres.map((c) => c.revenue), backgroundColor: PALETTE[1] },
                    { label: 'Direct cost', data: centres.map((c) => c.direct_cost), backgroundColor: PALETTE[3] },
                    { label: 'Shared cost', data: centres.map((c) => c.allocated_shared_cost), backgroundColor: PALETTE[0] },
                    { label: 'Depreciation', data: centres.map((c) => c.depreciation), backgroundColor: PALETTE[4] },
                ],
            },
            options: { plugins: { legend: { display: true, position: 'bottom' } } },
        });
    } else {
        destroyChart('plCentreChart');
    }

    renderTable('plWastageTable', [
        { key: 'product', label: 'Product' },
        { key: 'quantity', label: 'Quantity', num: true, render: (r) => formatNum(r.quantity) },
        { key: 'value', label: 'Value', num: true, render: (r) => formatRs(r.value) },
    ], data.wastage_detail, { emptyMessage: 'No waste recorded this period.' });

    document.getElementById('plCashCredit').innerHTML = [
        ['Cash collected', data.cash_vs_credit.cash_collected], ['Credit given this period', data.cash_vs_credit.credit_given],
        ['Credit outstanding (all-time)', data.cash_vs_credit.credit_outstanding_now],
    ].map(([label, value]) => `<div class="dash-kpi-tile"><div class="label">${escapeHtml(label)}</div><div class="value">${formatRs(value)}</div></div>`).join('');

    const dq = data.data_quality;
    document.getElementById('plNavBadge').style.display = dq.has_issues ? 'inline-block' : 'none';
    if (!dq.has_issues) {
        document.getElementById('plDataQuality').innerHTML = `<div class="dash-alert-clear"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 6L9 17l-5-5"/></svg><br>No data-quality gaps found for this period.</div>`;
    } else {
        const rows = Object.keys(PL_DQ_LABELS).filter((key) => dq[key] > 0).map((key) => `<tr><td>${escapeHtml(PL_DQ_LABELS[key])}</td><td class="num">${formatNum(dq[key])}</td></tr>`).join('');
        document.getElementById('plDataQuality').innerHTML = `<table class="dash-table"><tbody>${rows}</tbody></table>`;
    }
}

// ── Profit & Loss: "By batch" view (season/batch costing) ──────────────
//
// A second, independent lens inside the same P&L tab -- not date-range-
// scoped at all (a batch's own lifecycle is what matters, not the
// topbar's period), so switching into this view doesn't touch state.start/
// state.end or re-trigger loadAll(). Loaded once, on first switch into it.

let plBatchListLoaded = false;

function switchPlView(view) {
    document.getElementById('plMonthlyView').style.display = view === 'monthly' ? '' : 'none';
    document.getElementById('plBatchView').style.display = view === 'batch' ? '' : 'none';
    if (view === 'batch' && !plBatchListLoaded) {
        plBatchListLoaded = true;
        loadBatchList();
    }
}

async function loadBatchList() {
    document.getElementById('plBatchListTable').innerHTML = skeletonHtml(3);
    let data;
    try {
        data = await fetchJSON('/api/v1/reports/batches/');
    } catch (e) {
        document.getElementById('plBatchListTable').innerHTML = errorStateHtml(e.message, 'loadBatchList');
        return;
    }
    renderTable('plBatchListTable', [
        { key: 'code', label: 'Code', render: (r) => `<a href="#" class="pl-batch-link" data-batch-code="${escapeHtml(r.code)}">${escapeHtml(r.code)}</a>` },
        { key: 'name', label: 'Name' },
        { key: 'status', label: 'Status' },
        { key: 'total_cost', label: 'Total cost', num: true, render: (r) => formatRs(r.total_cost) },
        { key: 'harvested_kg', label: 'Harvested (kg)', num: true, render: (r) => formatNum(r.harvested_kg) },
        { key: 'cost_per_kg', label: 'Cost/kg', num: true, render: (r) => r.cost_per_kg == null ? '—' : formatRs(r.cost_per_kg) },
        { key: 'revenue', label: 'Revenue', num: true, render: (r) => formatRs(r.revenue) },
        { key: 'profit_to_date', label: 'Profit to date', num: true, render: (r) => r.profit_to_date == null ? '—' : formatRs(r.profit_to_date) },
    ], data.batches, { emptyMessage: 'No crop batches yet -- these come from ABMS.' });
}

async function loadBatchDetail(code) {
    const card = document.getElementById('plBatchDetailCard');
    card.style.display = 'block';
    document.getElementById('plBatchDetailTitle').textContent = code;
    document.getElementById('exportBatchDetailBtn').href = `/api/v1/reports/batches/${encodeURIComponent(code)}/?export=csv`;
    document.getElementById('plBatchDetailKpis').innerHTML = skeletonHtml(1, 70);
    document.getElementById('plBatchDetailTable').innerHTML = skeletonHtml(2);
    document.getElementById('plBatchDetailNotes').innerHTML = '';
    card.scrollIntoView({ behavior: REDUCED_MOTION ? 'auto' : 'smooth', block: 'nearest' });

    let data;
    try {
        data = await fetchJSON(`/api/v1/reports/batches/${encodeURIComponent(code)}/`);
    } catch (e) {
        document.getElementById('plBatchDetailTable').innerHTML = emptyStateHtml(e.message);
        return;
    }

    document.getElementById('plBatchDetailTitle').textContent =
        `${data.batch.name || data.batch.code} (${data.batch.code}) — ${data.batch.status}`;

    const kpi = (label, value) => `<div class="dash-kpi-tile"><div class="label">${escapeHtml(label)}</div><div class="value">${value == null ? '—' : formatRs(value)}</div></div>`;
    document.getElementById('plBatchDetailKpis').innerHTML = [
        `<div class="dash-kpi-tile"><div class="label">Harvested (kg)</div><div class="value">${formatNum(data.harvested_kg)}</div></div>`,
        kpi('Total cost', data.cost.total),
        kpi('Cost / kg', data.cost_per_kg),
        kpi('Revenue', data.revenue),
        kpi('Profit to date', data.profit_to_date),
        kpi('Break-even price', data.break_even_price),
    ].join('');

    const catRows = Object.entries(data.cost.by_category).map(([category, amount]) => ({ category, amount }));
    renderTable('plBatchDetailTable', [
        { key: 'category', label: 'Category' },
        { key: 'amount', label: 'Amount', num: true, render: (r) => formatRs(r.amount) },
    ], catRows, { emptyMessage: 'No costs recorded for this batch yet.' });

    document.getElementById('plBatchDetailNotes').innerHTML = (data.notes || [])
        .map((n) => `<p class="dash-note">${escapeHtml(n)}</p>`).join('');
}

// ── Filter options (operator dropdown) ─────────────────────────────────

async function loadFilterOptions() {
    try {
        const data = await fetchJSON('/api/v1/reports/filter-options/');
        const sel = document.getElementById('operatorSelect');
        data.operators.forEach((op) => {
            const opt = document.createElement('option');
            opt.value = op.id; opt.textContent = op.name;
            sel.appendChild(opt);
        });
    } catch (e) { /* non-fatal -- operator filter just stays empty */ }
}

// ── Init ────────────────────────────────────────────────────────────────

function initControls() {
    document.getElementById('presetSelect').addEventListener('change', (e) => applyPreset(e.target.value));
    document.getElementById('applyCustomRangeBtn').addEventListener('click', applyCustomBsRange);

    function wireSegmented(id, stateKey, onChange) {
        document.querySelectorAll(`#${id} button`).forEach((btn) => {
            btn.addEventListener('click', () => {
                document.querySelectorAll(`#${id} button`).forEach((b) => b.setAttribute('aria-pressed', 'false'));
                btn.setAttribute('aria-pressed', 'true');
                state[stateKey] = btn.dataset.value;
                if (onChange) onChange(); else loadAll();
            });
        });
    }
    wireSegmented('channelSeg', 'channel');
    wireSegmented('granularitySeg', 'granularity');
    wireSegmented('topProductsBySeg', null, () => {
        topProductsBy = document.querySelector('#topProductsBySeg button[aria-pressed="true"]').dataset.value;
        refreshTopProductsChart();
    });
    wireSegmented('plViewSeg', null, () => {
        switchPlView(document.querySelector('#plViewSeg button[aria-pressed="true"]').dataset.value);
    });
    // Event delegation (not a per-row listener) so re-sorting plBatchListTable
    // (renderTable() fully redraws its <tbody> on every sort click) never
    // loses the click handler on a batch's code link.
    document.getElementById('plBatchListTable').addEventListener('click', (e) => {
        const link = e.target.closest('.pl-batch-link');
        if (link) { e.preventDefault(); loadBatchDetail(link.dataset.batchCode); }
    });

    document.getElementById('paymentTypeSelect').addEventListener('change', (e) => { state.payment_type = e.target.value; loadAll(); });
    document.getElementById('customerTypeSelect').addEventListener('change', (e) => { state.customer_type = e.target.value; loadAll(); });
    document.getElementById('operatorSelect').addEventListener('change', (e) => { state.operator = e.target.value; loadAll(); });
    document.getElementById('compareToggle').addEventListener('change', (e) => { state.compare = e.target.checked; loadAll(); });
    document.getElementById('refreshBtn').addEventListener('click', loadAll);
    document.getElementById('printBtn').addEventListener('click', () => window.print());
    document.getElementById('resetFiltersBtn').addEventListener('click', () => {
        state.payment_type = ''; state.customer_type = ''; state.operator = '';
        document.getElementById('paymentTypeSelect').value = '';
        document.getElementById('customerTypeSelect').value = '';
        document.getElementById('operatorSelect').value = '';
        loadAll();
    });

    document.getElementById('sidebarToggle').addEventListener('click', () => {
        const open = document.getElementById('dashSidebar').classList.toggle('open');
        document.getElementById('sidebarBackdrop').classList.toggle('open', open);
        document.getElementById('sidebarToggle').setAttribute('aria-expanded', String(open));
    });
    document.getElementById('sidebarBackdrop').addEventListener('click', closeMobileSidebar);
}

function initTodayChip() {
    const bs = BsCalendar.todayBs();
    document.getElementById('todayBsChip').textContent = 'आज ' + BsCalendar.formatBs(bs);
}

document.addEventListener('DOMContentLoaded', () => {
    initTodayChip();
    initNav();
    initBsGridNav();
    renderBsGrid();
    initControls();
    loadFilterOptions();
    applyPreset('last30');
});
