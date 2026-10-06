'use strict';
/* Staff Reports & Dashboard (/dashboard/) -- vanilla JS, no build step
   (see CLAUDE.md's Stack section). Talks only to the read-only
   GET /api/v1/reports/* endpoints; never calls anything that writes to
   the POS, checkout, or inventory ledger. This page is never cached by
   a service worker (there isn't one registered here) and has no offline
   mode -- any fetch failure shows a plain "needs a connection" message.
*/

// ── Brand palette (derived from style.css's --moss/--gold/--forest/
//    --mist, extended with two extra hues) -- picked to stay
//    distinguishable under common colorblindness simulations (not just
//    "looks different to me"), and every doughnut/pie also gets an HTML
//    legend with values/percentages so no reading depends on color alone. ──
const PALETTE = ['#1a2f1e', '#c9a84c', '#4a7c59', '#3a6ea5', '#b5651d', '#8aa88e', '#7a4a8c', '#a3311a', '#e8cc84', '#5c7a99'];
const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (window.Chart) {
    Chart.defaults.font.family = "'DM Sans', sans-serif";
    Chart.defaults.color = '#1a2f1e';
    if (REDUCED_MOTION) Chart.defaults.animation = false;
}

// ── Number / date formatting ─────────────────────────────────────────

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
function formatNum(n, decimals) {
    if (decimals) return (n || 0).toFixed(decimals);
    return formatIndianGrouping(n || 0);
}
function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function formatLocalDateTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
}

// Nepal-local "today"/date-math without depending on the browser's own
// timezone: shift the epoch by the fixed +5:45 offset, then read fields
// with the UTC getters, so the UTC fields ARE Nepal wall-clock fields.
function nepalDateParts(offsetDays) {
    const d = new Date(Date.now() + (5 * 60 + 45) * 60000 + (offsetDays || 0) * 86400000);
    return { y: d.getUTCFullYear(), m: d.getUTCMonth(), day: d.getUTCDate() };
}
function toYmd(y, m, day) {
    return `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}
function partsToYmd(p) { return toYmd(p.y, p.m, p.day); }
function daysInMonth(y, m) { return new Date(Date.UTC(y, m + 1, 0)).getUTCDate(); }

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
        try {
            const body = await res.json();
            if (body && body.message) msg = body.message;
        } catch (e) { /* non-JSON error body, keep generic message */ }
        throw new ReportFetchError(msg);
    }
    return res.json();
}

// ── Global filter state ──────────────────────────────────────────────

const state = { start: null, end: null, channel: 'all', granularity: 'day', compare: false, tab: 'overview' };
let filtersSignature = '';
const loadedTabs = {};

function buildParams(extra) {
    const p = new URLSearchParams({
        start: state.start, end: state.end, channel: state.channel,
        granularity: state.granularity, compare: state.compare ? 'true' : 'false',
    });
    if (extra) Object.keys(extra).forEach((k) => p.set(k, extra[k]));
    return p.toString();
}

function applyPreset(preset) {
    document.querySelectorAll('.dash-preset-btn').forEach((b) => b.classList.toggle('active', b.dataset.preset === preset));
    const today = nepalDateParts(0);
    let start, end;
    if (preset === 'today') { start = end = partsToYmd(today); }
    else if (preset === 'yesterday') { start = end = partsToYmd(nepalDateParts(-1)); }
    else if (preset === 'last7') { start = partsToYmd(nepalDateParts(-6)); end = partsToYmd(today); }
    else if (preset === 'last30') { start = partsToYmd(nepalDateParts(-29)); end = partsToYmd(today); }
    else if (preset === 'thismonth') { start = toYmd(today.y, today.m, 1); end = partsToYmd(today); }
    else if (preset === 'lastmonth') {
        let y = today.y, m = today.m - 1;
        if (m < 0) { m = 11; y -= 1; }
        start = toYmd(y, m, 1);
        end = toYmd(y, m, daysInMonth(y, m));
    }
    document.getElementById('startDate').value = start;
    document.getElementById('endDate').value = end;
    loadAll();
}

function validateFilters() {
    const errEl = document.getElementById('filterError');
    errEl.textContent = '';
    const start = document.getElementById('startDate').value;
    const end = document.getElementById('endDate').value;
    if (!start || !end) { errEl.textContent = 'Please choose both a start and end date.'; return false; }
    if (start > end) { errEl.textContent = "'Start' must not be after 'end'."; return false; }
    const days = (new Date(end) - new Date(start)) / 86400000 + 1;
    if (days > 366) { errEl.textContent = 'Date range cannot exceed 366 days.'; return false; }
    state.start = start;
    state.end = end;
    return true;
}

function loadAll() {
    if (!validateFilters()) return;
    state.channel = document.getElementById('channelSelect').value;
    state.granularity = document.getElementById('granularitySelect').value;
    state.compare = document.getElementById('compareToggle').checked;

    const sig = JSON.stringify(state);
    if (sig !== filtersSignature) {
        filtersSignature = sig;
        Object.keys(loadedTabs).forEach((k) => delete loadedTabs[k]);
    }
    document.getElementById('dashConnError').style.display = 'none';
    document.getElementById('dashPrintRange').textContent = `${state.start} to ${state.end} (${state.channel}, ${state.granularity})`;
    loadAlertsAndCurrentTab();
}

async function loadAlertsAndCurrentTab() {
    renderAlertsSkeleton();
    try {
        const summary = await fetchJSON(`/api/v1/reports/summary/?${buildParams()}`);
        window.__lastSummary = summary;
        renderAlerts(summary.alerts);
        if (state.tab === 'overview') renderOverview(summary);
        loadedTabs.overview = true;
        setLastUpdated();
    } catch (e) {
        showConnError(e.message);
    }
    if (state.tab !== 'overview' && !loadedTabs[state.tab]) {
        loadTab(state.tab);
    }
}

function setLastUpdated() {
    document.getElementById('lastUpdated').textContent = 'Last updated: ' + new Date().toLocaleTimeString('en-GB');
}

function showConnError(message) {
    const el = document.getElementById('dashConnError');
    el.style.display = 'block';
    el.innerHTML = `${escapeHtml(message)} <button type="button" onclick="loadAll()">Retry</button>`;
}

// ── Tabs ──────────────────────────────────────────────────────────────

const TAB_LOADERS = {
    overview: async () => { if (window.__lastSummary) renderOverview(window.__lastSummary); },
    sales: loadSales,
    products: loadProducts,
    credit: loadCredit,
    inventory: loadInventory,
    orders: loadOrders,
};

function loadTab(tab) {
    loadedTabs[tab] = true;
    const loader = TAB_LOADERS[tab];
    if (loader) loader().catch((e) => showConnError(e.message));
}

function initTabs() {
    document.querySelectorAll('.dash-tab').forEach((btn) => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        btn.addEventListener('keydown', (ev) => {
            const tabs = Array.from(document.querySelectorAll('.dash-tab'));
            const i = tabs.indexOf(btn);
            if (ev.key === 'ArrowRight') { tabs[(i + 1) % tabs.length].focus(); tabs[(i + 1) % tabs.length].click(); }
            if (ev.key === 'ArrowLeft') { tabs[(i - 1 + tabs.length) % tabs.length].focus(); tabs[(i - 1 + tabs.length) % tabs.length].click(); }
        });
    });
}

function switchTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.dash-tab').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    document.querySelectorAll('.dash-tabpanel').forEach((p) => p.classList.toggle('active', p.id === `panel-${tab}`));
    document.getElementById('dashPrintRange').textContent = `${state.start} to ${state.end} (${state.channel}, ${state.granularity}) — ${tab} section`;
    if (!loadedTabs[tab]) loadTab(tab);
}

// ── Rendering: skeleton / empty / error helpers ──────────────────────

function skeletonHtml(count, height) {
    return Array.from({ length: count || 1 }).map(() => `<div class="dash-skeleton" style="height:${height || 60}px;margin-bottom:8px;"></div>`).join('');
}
function emptyStateHtml(message) {
    return `<div class="dash-empty-state">${escapeHtml(message || 'Nothing here for this date range.')}</div>`;
}
function errorStateHtml(message, retryFn) {
    const id = 'retry_' + Math.random().toString(36).slice(2);
    window[id] = retryFn;
    return `<div class="dash-error-state">${escapeHtml(message)}<br><button type="button" onclick="${id}()">Retry</button></div>`;
}

// ── KPI cards ──────────────────────────────────────────────────────────

function kpiCard(label, info, formatter) {
    formatter = formatter || formatRs;
    const dir = info.direction;
    // direction is null when the Compare toggle is off (or for an
    // always-live snapshot like outstanding credit) -- no delta line at
    // all in that case, not a synthetic "new vs previous period".
    let deltaHtml = '';
    if (dir != null) {
        const arrow = dir === 'up' ? '▲' : dir === 'down' ? '▼' : dir === 'new' ? '●' : '―';
        const dirText = dir === 'up' ? 'up' : dir === 'down' ? 'down' : dir === 'new' ? 'new' : 'flat';
        const deltaText = info.delta_pct == null
            ? (dir === 'new' ? 'new vs previous period' : 'no change vs previous period')
            : `${arrow} ${Math.abs(info.delta_pct)}% ${dirText} vs previous period`;
        deltaHtml = `<div class="dash-kpi-delta ${dir}">${escapeHtml(deltaText)}</div>`;
    }
    return `
        <div class="dash-kpi-card">
            <div class="dash-kpi-label">${escapeHtml(label)}</div>
            <div class="dash-kpi-value">${formatter(info.current)}</div>
            ${deltaHtml}
        </div>`;
}

function renderAlertsSkeleton() {
    document.getElementById('alertsStrip').innerHTML = skeletonHtml(1, 50);
}
function renderAlerts(alerts) {
    const el = document.getElementById('alertsStrip');
    el.innerHTML = `
        <div class="dash-alert-pill ${alerts.low_stock_count > 0 ? 'warn' : ''}"><span class="count">${alerts.low_stock_count}</span>low-stock product${alerts.low_stock_count === 1 ? '' : 's'}</div>
        <div class="dash-alert-pill ${alerts.pending_online_orders > 0 ? 'warn' : ''}"><span class="count">${alerts.pending_online_orders}</span>pending/unfulfilled online order${alerts.pending_online_orders === 1 ? '' : 's'}</div>
        <div class="dash-alert-pill ${alerts.overdue_credit_total > 0 ? 'warn' : ''}"><span class="count">${formatRs(alerts.overdue_credit_total)}</span>total outstanding credit</div>
    `;
}

// ── Chart helper (auto "view as table" alternative) ──────────────────

function destroyChart(canvasId) {
    if (window.__charts && window.__charts[canvasId]) { window.__charts[canvasId].destroy(); }
}
function makeChart(canvasId, config) {
    window.__charts = window.__charts || {};
    destroyChart(canvasId);
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;
    config.options = config.options || {};
    config.options.maintainAspectRatio = false;
    config.options.animation = REDUCED_MOTION ? false : config.options.animation;
    const chart = new Chart(canvas, config);
    window.__charts[canvasId] = chart;
    return chart;
}

function doughnutLegendHtml(labels, values, formatter) {
    formatter = formatter || formatNum;
    const total = values.reduce((a, b) => a + b, 0) || 1;
    return `<ul style="list-style:none;padding:0;margin:8px 0 0;font-size:0.8rem;">${labels.map((l, i) => {
        const pct = ((values[i] / total) * 100).toFixed(1);
        return `<li style="display:flex;align-items:center;gap:6px;margin-bottom:3px;"><span style="width:10px;height:10px;border-radius:2px;background:${PALETTE[i % PALETTE.length]};display:inline-block;"></span>${escapeHtml(l)}: <strong>${formatter(values[i])}</strong> (${pct}%)</li>`;
    }).join('')}</ul>`;
}

// ── Sortable / exportable table helper ────────────────────────────────

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

// ── Overview tab ──────────────────────────────────────────────────────

function renderOverview(summary) {
    const k = summary.kpis;
    document.getElementById('overviewKpis').innerHTML = [
        kpiCard('Total sales (POS)', k.total_sales),
        kpiCard('Transactions', k.transactions, (v) => formatNum(v)),
        kpiCard('AOV', k.aov),
        kpiCard('Units sold', k.units_count, (v) => formatNum(v)),
        kpiCard('Weight sold (kg)', k.weight_kg, (v) => formatNum(v, 2)),
        kpiCard('Cash collected', k.cash_collected),
        kpiCard('Credit given', k.credit_given),
        kpiCard('Repayments', k.repayments),
        kpiCard('Outstanding credit', k.outstanding_credit),
        kpiCard('Discounts given', k.discounts_given),
        kpiCard('Online orders', k.online_order_count, (v) => formatNum(v)),
    ].join('');

    const series = summary.sales_over_time;
    if (!series.current || !series.current.length) {
        document.getElementById('overviewTrendChart').closest('.dash-chart-wrap').style.display = 'none';
        document.querySelector('[data-chart="overviewTrendChart"]').style.display = 'none';
    } else {
        document.getElementById('overviewTrendChart').closest('.dash-chart-wrap').style.display = '';
        document.querySelector('[data-chart="overviewTrendChart"]').style.display = '';
        const labels = series.current.map((p) => p.date);
        const datasets = [{ label: 'Sales', data: series.current.map((p) => p.total), borderColor: PALETTE[0], backgroundColor: 'transparent', tension: 0.2 }];
        if (series.previous && series.previous.length) {
            datasets.push({ label: 'Previous period', data: series.previous.map((p) => p.total), borderColor: PALETTE[2], borderDash: [6, 4], backgroundColor: 'transparent', tension: 0.2 });
        }
        makeChart('overviewTrendChart', { type: 'line', data: { labels, datasets }, options: { plugins: { legend: { display: true } } } });
        const tableBtn = document.querySelector('[data-chart="overviewTrendChart"]');
        tableBtn.onclick = () => {
            const tbl = document.getElementById('overviewTrendTable');
            const showing = tbl.style.display !== 'none';
            tbl.style.display = showing ? 'none' : 'block';
            if (!showing) renderTable('overviewTrendTable', [{ key: 'date', label: 'Date' }, { key: 'total', label: 'Sales', num: true, render: (r) => formatRs(r.total) }], series.current);
        };
    }

    if (summary.payment_mix.length) {
        const labels = summary.payment_mix.map((p) => p.method);
        const values = summary.payment_mix.map((p) => p.total);
        makeChart('paymentMixChart', { type: 'doughnut', data: { labels, datasets: [{ data: values, backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
    } else {
        destroyChart('paymentMixChart');
        document.getElementById('paymentMixChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml('No POS payments in this period.');
    }

    const cs = summary.channel_split;
    makeChart('channelSplitChart', { type: 'doughnut', data: { labels: ['POS', 'Online'], datasets: [{ data: [cs.pos, cs.online], backgroundColor: [PALETTE[0], PALETTE[3]] } ] }, options: { plugins: { legend: { display: false } } } });

    if (summary.top_products.length) {
        makeChart('topProductsChart', {
            type: 'bar',
            data: { labels: summary.top_products.map((p) => p.name), datasets: [{ label: 'Qty', data: summary.top_products.map((p) => p.qty + p.weight_kg), backgroundColor: PALETTE[1] }] },
            options: { indexAxis: 'y', plugins: { legend: { display: false } } },
        });
    } else {
        destroyChart('topProductsChart');
        document.getElementById('topProductsChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }
}

// ── Sales tab ──────────────────────────────────────────────────────────

let salesPage = 1;

async function loadSales() {
    document.getElementById('recentSalesTable').innerHTML = skeletonHtml(4);
    document.getElementById('byOperatorTable').innerHTML = skeletonHtml(3);
    document.getElementById('exportSalesBtn').href = `/api/v1/reports/sales-trend/?${buildParams({ export: 'csv' })}`;
    salesPage = 1;
    try {
        const data = await fetchJSON(`/api/v1/reports/sales-trend/?${buildParams()}`);
        renderSalesTrend(data.trend);
        renderSalesTable(data.recent_sales);
        renderTable('byOperatorTable',
            [{ key: 'operator', label: 'Operator' }, { key: 'total', label: 'Total', num: true, render: (r) => formatRs(r.total) }, { key: 'count', label: 'Sales', num: true }],
            data.by_operator, { emptyMessage: 'No sales in this period.' });
    } catch (e) {
        document.getElementById('recentSalesTable').innerHTML = errorStateHtml(e.message, loadSales);
    }
}

function renderSalesTrend(trend) {
    const series = trend.series;
    if (series.current && series.current.length) {
        const labels = series.current.map((p) => p.date);
        const datasets = [{ label: 'Revenue', data: series.current.map((p) => p.total), borderColor: PALETTE[0], backgroundColor: 'transparent', tension: 0.2 }];
        if (series.previous && series.previous.length) datasets.push({ label: 'Previous period', data: series.previous.map((p) => p.total), borderColor: PALETTE[2], borderDash: [6, 4], backgroundColor: 'transparent', tension: 0.2 });
        makeChart('salesTrendChart', { type: 'line', data: { labels, datasets } });
    } else {
        destroyChart('salesTrendChart');
        document.getElementById('salesTrendChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
    }
    makeChart('salesByHourChart', { type: 'bar', data: { labels: Array.from({ length: 24 }, (_, h) => h + ':00'), datasets: [{ label: 'Sales', data: trend.by_hour, backgroundColor: PALETTE[1] }] }, options: { plugins: { legend: { display: false } } } });
    makeChart('salesByDowChart', { type: 'bar', data: { labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], datasets: [{ label: 'Sales', data: trend.by_day_of_week, backgroundColor: PALETTE[3] }] }, options: { plugins: { legend: { display: false } } } });
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

// ── Products tab ────────────────────────────────────────────────────────

async function loadProducts() {
    document.getElementById('productTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportProductsBtn').href = `/api/v1/reports/products/?${buildParams({ export: 'csv' })}`;
    document.getElementById('topProductsBySelect').onchange = refreshTopProductsChart;
    try {
        const data = await fetchJSON(`/api/v1/reports/products/?${buildParams({ by: document.getElementById('topProductsBySelect').value })}`);
        window.__productsData = data;
        renderTopProductsChart(data.top_products, document.getElementById('topProductsBySelect').value);
        renderCategoryChart(data.category_breakdown);
        renderTable('slowMoversTable', [{ key: 'name', label: 'Product' }], data.slow_movers, { emptyMessage: 'Everything available sold at least once this period.' });
        renderTable('productTable', [
            { key: 'name', label: 'Product' },
            { key: 'qty', label: 'Qty', num: true },
            { key: 'weight_kg', label: 'Weight (kg)', num: true },
            { key: 'revenue_est', label: 'Revenue (est.)', num: true, render: (r) => formatRs(r.revenue_est) },
            { key: 'share_pct', label: 'Share %', num: true, render: (r) => r.share_pct + '%' },
            { key: 'trend_pct', label: 'Trend', num: true, render: (r) => r.trend.delta_pct == null ? (r.trend.direction === 'new' ? 'new' : '—') : `${r.trend.delta_pct}%` },
        ], data.product_table.map((r) => ({ ...r, trend_pct: r.trend.delta_pct || 0 })), { emptyMessage: 'No sales in this period.' });
        renderOffersTable(data.offers_performance);
    } catch (e) {
        document.getElementById('productTable').innerHTML = errorStateHtml(e.message, loadProducts);
    }
}

function refreshTopProductsChart() {
    const by = document.getElementById('topProductsBySelect').value;
    fetchJSON(`/api/v1/reports/products/?${buildParams({ by })}`).then((data) => renderTopProductsChart(data.top_products, by)).catch((e) => showConnError(e.message));
}

function renderTopProductsChart(products, by) {
    if (!products.length) { destroyChart('productsTopChart'); document.getElementById('productsTopChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); return; }
    const values = products.map((p) => by === 'revenue' ? p.revenue_est : (p.qty + p.weight_kg));
    makeChart('productsTopChart', { type: 'bar', data: { labels: products.map((p) => p.name), datasets: [{ label: by === 'revenue' ? 'Revenue (est.)' : 'Qty', data: values, backgroundColor: PALETTE[1] }] }, options: { indexAxis: 'y', plugins: { legend: { display: false } } } });
}

function renderCategoryChart(categories) {
    const wrap = document.getElementById('categoryChart').closest('.dash-card');
    if (!categories.length) { destroyChart('categoryChart'); document.getElementById('categoryChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml(); return; }
    makeChart('categoryChart', { type: 'doughnut', data: { labels: categories.map((c) => c.category), datasets: [{ data: categories.map((c) => c.revenue_est), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: false } } } });
    let legend = wrap.querySelector('.dash-doughnut-legend');
    if (!legend) { legend = document.createElement('div'); legend.className = 'dash-doughnut-legend'; wrap.appendChild(legend); }
    legend.innerHTML = doughnutLegendHtml(categories.map((c) => c.category), categories.map((c) => c.revenue_est), formatRs);
}

function renderOffersTable(perf) {
    const el = document.getElementById('offersTable');
    const offersHtml = perf.offers.length
        ? `<h4 style="margin:10px 0 4px;font-size:0.85rem;">Offers</h4><table class="dash-table"><thead><tr><th>Offer</th><th class="num">Uses</th><th>Discount given</th></tr></thead><tbody>${perf.offers.map((o) => `<tr><td>${escapeHtml(o.title)}</td><td class="num">${o.uses}</td><td>not tracked</td></tr>`).join('')}</tbody></table>`
        : '';
    const couponsHtml = perf.coupons.length
        ? `<h4 style="margin:10px 0 4px;font-size:0.85rem;">Coupons</h4><table class="dash-table"><thead><tr><th>Code</th><th class="num">Uses</th><th class="num">Discount given</th></tr></thead><tbody>${perf.coupons.map((c) => `<tr><td>${escapeHtml(c.code)}</td><td class="num">${c.uses}</td><td class="num">${formatRs(c.discount_given)}</td></tr>`).join('')}</tbody></table>`
        : '';
    el.innerHTML = (offersHtml + couponsHtml) || emptyStateHtml('No offers or coupons used in this period.');
}

// ── Credit tab ──────────────────────────────────────────────────────────

async function loadCredit() {
    document.getElementById('creditKpis').innerHTML = skeletonHtml(1, 90);
    document.getElementById('creditCustomersTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportCreditBtn').href = `/api/v1/reports/credit/?${buildParams({ export: 'csv' })}`;
    try {
        const data = await fetchJSON(`/api/v1/reports/credit/?${buildParams()}`);
        const k = data.kpis;
        document.getElementById('creditKpis').innerHTML = [
            kpiCard('Credit given', k.given), kpiCard('Repaid', k.repaid), kpiCard('Outstanding (all-time)', k.outstanding),
            kpiCard('Customers with balance', k.customers_with_balance, (v) => formatNum(v)),
        ].join('');

        const buckets = data.ageing_buckets;
        makeChart('ageingChart', { type: 'bar', data: { labels: ['0-30 days', '31-60 days', '61-90 days', '90+ days'], datasets: [{ label: 'Customers', data: [buckets['0-30'], buckets['31-60'], buckets['61-90'], buckets['90+']], backgroundColor: [PALETTE[2], PALETTE[1], PALETTE[4], PALETTE[7]] }] }, options: { plugins: { legend: { display: false } } } });

        if (data.trend.length) {
            makeChart('creditTrendChart', { type: 'line', data: { labels: data.trend.map((t) => t.date), datasets: [
                { label: 'Given', data: data.trend.map((t) => t.given), borderColor: PALETTE[4], backgroundColor: 'transparent' },
                { label: 'Repaid', data: data.trend.map((t) => t.repaid), borderColor: PALETTE[2], backgroundColor: 'transparent' },
            ] } });
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
    } catch (e) {
        document.getElementById('creditCustomersTable').innerHTML = errorStateHtml(e.message, loadCredit);
    }
}

// ── Inventory tab ───────────────────────────────────────────────────────

async function loadInventory() {
    document.getElementById('stockTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportInventoryBtn').href = `/api/v1/reports/inventory/?${buildParams({ export: 'csv' })}`;
    try {
        const data = await fetchJSON(`/api/v1/reports/inventory/?${buildParams()}`);
        if (data.movements_by_type.length) {
            makeChart('movementsChart', { type: 'bar', data: { labels: data.movements_by_type.map((m) => m.movement_type), datasets: [{ label: 'Quantity', data: data.movements_by_type.map((m) => m.total), backgroundColor: PALETTE[1] }] }, options: { plugins: { legend: { display: false } } } });
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
    } catch (e) {
        document.getElementById('stockTable').innerHTML = errorStateHtml(e.message, loadInventory);
    }
}

// ── Online Orders tab ─────────────────────────────────────────────────

let ordersPage = 1;

async function loadOrders() {
    document.getElementById('recentOrdersTable').innerHTML = skeletonHtml(4);
    document.getElementById('exportOrdersBtn').href = `/api/v1/reports/orders/?${buildParams({ export: 'csv' })}`;
    ordersPage = 1;
    try {
        const data = await fetchJSON(`/api/v1/reports/orders/?${buildParams()}`);
        const statuses = Object.keys(data.by_status);
        if (statuses.length) {
            makeChart('ordersByStatusChart', { type: 'doughnut', data: { labels: statuses, datasets: [{ data: statuses.map((s) => data.by_status[s]), backgroundColor: PALETTE }] }, options: { plugins: { legend: { display: true } } } });
        } else {
            destroyChart('ordersByStatusChart');
            document.getElementById('ordersByStatusChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
        }
        if (data.over_time.length) {
            makeChart('ordersOverTimeChart', { type: 'line', data: { labels: data.over_time.map((o) => o.date), datasets: [{ label: 'Orders', data: data.over_time.map((o) => o.count), borderColor: PALETTE[0], backgroundColor: 'transparent' }] }, options: { plugins: { legend: { display: false } } } });
        } else {
            destroyChart('ordersOverTimeChart');
            document.getElementById('ordersOverTimeChart').closest('.dash-chart-wrap').innerHTML = emptyStateHtml();
        }
        renderOrdersTable(data.recent_orders);
    } catch (e) {
        document.getElementById('recentOrdersTable').innerHTML = errorStateHtml(e.message, loadOrders);
    }
}

function renderOrdersTable(page) {
    const el = document.getElementById('recentOrdersTable');
    if (!page.results.length) { el.innerHTML = emptyStateHtml('No orders in this period.'); document.getElementById('recentOrdersPager').innerHTML = ''; return; }
    el.innerHTML = `<table class="dash-table"><thead><tr><th>Order #</th><th>Name</th><th>Status</th><th>Ordered</th><th>Admin</th></tr></thead><tbody>${
        page.results.map((r) => `<tr><td>${escapeHtml(r.order_number)}</td><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.status)}</td><td>${formatLocalDateTime(r.ordered_at)}</td><td><a href="/admin/shop/productorder/${r.id}/change/" target="_blank" rel="noopener">View</a></td></tr>`).join('')
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

// ── Init ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    document.querySelectorAll('.dash-preset-btn').forEach((b) => b.addEventListener('click', () => applyPreset(b.dataset.preset)));
    document.getElementById('refreshBtn').addEventListener('click', loadAll);
    document.getElementById('channelSelect').addEventListener('change', loadAll);
    document.getElementById('granularitySelect').addEventListener('change', loadAll);
    document.getElementById('compareToggle').addEventListener('change', loadAll);
    document.getElementById('startDate').addEventListener('change', () => { document.querySelectorAll('.dash-preset-btn').forEach((b) => b.classList.remove('active')); loadAll(); });
    document.getElementById('endDate').addEventListener('change', () => { document.querySelectorAll('.dash-preset-btn').forEach((b) => b.classList.remove('active')); loadAll(); });
    applyPreset('last30');
});
