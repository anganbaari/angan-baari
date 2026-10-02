// Shared IndexedDB-backed offline queue for POS sale submission.
//
// Loaded two ways, deliberately a classic (non-module) script so both work
// unchanged against the exact same file:
//   - <script src="..."> in templates/pos.html (page context -- functions
//     land as plain globals, i.e. window.posQueueEnqueue etc.)
//   - importScripts(...) inside static/js/pos-sw.js (service worker context
//     -- same functions land as globals on the worker's `self`)
//
// Phase 1 scope only: this queue exists so a sale that fails to reach
// POST /api/v1/sales/ because of a NETWORK failure (not a real validation
// error) isn't lost -- it's retried later, in order, relying on
// client_sale_id idempotency to make a double-send harmless. It does not
// do any catalog/offline-browsing caching (that's Phase 2).

var POS_QUEUE_DB_NAME = 'angan-baari-pos';
var POS_QUEUE_DB_VERSION = 1;
var POS_QUEUE_STORE = 'queuedSales';

function posQueueOpenDB() {
    return new Promise(function (resolve, reject) {
        var req = indexedDB.open(POS_QUEUE_DB_NAME, POS_QUEUE_DB_VERSION);
        req.onupgradeneeded = function () {
            var db = req.result;
            if (!db.objectStoreNames.contains(POS_QUEUE_STORE)) {
                db.createObjectStore(POS_QUEUE_STORE, { keyPath: 'client_sale_id' });
            }
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
    });
}

function posQueueEnqueue(payload) {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_QUEUE_STORE, 'readwrite');
            tx.objectStore(POS_QUEUE_STORE).put({
                client_sale_id: payload.client_sale_id,
                payload: payload,
                queuedAt: new Date().toISOString(),
                status: 'pending', // 'pending' | 'needsReauth' | 'failed'
                lastError: null,
                lastAttemptAt: null,
            });
            tx.oncomplete = function () { resolve(); };
            tx.onerror = function () { reject(tx.error); };
        });
    });
}

function posQueueList() {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_QUEUE_STORE, 'readonly');
            var req = tx.objectStore(POS_QUEUE_STORE).getAll();
            req.onsuccess = function () {
                // queuedAt is an ISO string -- lexical sort matches chronological order.
                resolve(req.result.sort(function (a, b) { return a.queuedAt < b.queuedAt ? -1 : 1; }));
            };
            req.onerror = function () { reject(req.error); };
        });
    });
}

function posQueueUpdate(clientSaleId, patch) {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_QUEUE_STORE, 'readwrite');
            var store = tx.objectStore(POS_QUEUE_STORE);
            var getReq = store.get(clientSaleId);
            getReq.onsuccess = function () {
                var record = getReq.result;
                if (!record) { resolve(); return; }
                store.put(Object.assign({}, record, patch));
            };
            tx.oncomplete = function () { resolve(); };
            tx.onerror = function () { reject(tx.error); };
        });
    });
}

function posQueueRemove(clientSaleId) {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_QUEUE_STORE, 'readwrite');
            tx.objectStore(POS_QUEUE_STORE).delete(clientSaleId);
            tx.oncomplete = function () { resolve(); };
            tx.onerror = function () { reject(tx.error); };
        });
    });
}

// document.cookie works in the page context; a service worker has no
// `document` at all, so it falls back to the newer cookieStore API
// (Chromium-only as of this writing -- Safari lacks it, which is also
// exactly where Background Sync itself isn't supported, so a SW-driven
// replay was never going to work there anyway; the page-context
// online/visibilitychange fallback in pos.html is what covers that browser
// instead, and it always has `document.cookie` available).
function posGetCsrfToken() {
    if (typeof document !== 'undefined' && document.cookie) {
        var match = document.cookie.match('(^|;)\\s*csrftoken\\s*=\\s*([^;]+)');
        if (match) return Promise.resolve(decodeURIComponent(match[2]));
    }
    if (typeof cookieStore !== 'undefined') {
        return cookieStore.get('csrftoken').then(function (c) { return c ? c.value : null; }).catch(function () { return null; });
    }
    return Promise.resolve(null);
}

// A failed queue entry otherwise only ever exists in this one device's
// IndexedDB -- see PosQueueFailureAlertView (api/views.py) for why this
// gets a best-effort Telegram ping rather than silently disappearing if
// the browser data is ever cleared. Deliberately fire-and-forget: if this
// call itself fails (also offline, say), the entry is still visibly
// 'failed' in the on-screen queue panel either way.
function posAlertQueueFailure(clientSaleId, payload, error) {
    return posGetCsrfToken().then(function (csrfToken) {
        return fetch('/api/v1/pos/queue/report-failed/', {
            method: 'POST',
            headers: Object.assign(
                { 'Content-Type': 'application/json' },
                csrfToken ? { 'X-CSRFToken': csrfToken } : {}
            ),
            body: JSON.stringify({ client_sale_id: clientSaleId, payload: payload, error: error }),
        }).catch(function () { /* best-effort */ });
    });
}

// Replays every 'pending' queued sale, in order, against POST
// /api/v1/sales/ -- sequential, not parallel, since a later sale's stock
// validity can depend on an earlier one in the same queue actually having
// gone through first.
//
// Per-entry outcome:
//   - 200/201 (success, including an idempotent replay of an already-
//     accepted sale) -> removed from the queue.
//   - 401/403 (session/terminal-unlock expired while offline) -> marked
//     'needsReauth' and left in the queue -- never silently dropped, never
//     retried forever; the UI must surface this so staff know to re-log-in
//     or re-enter their PIN before it can sync.
//   - any other 4xx (a real validation/stock/409 failure) -> marked
//     'failed' and left in the queue for staff to review/dismiss -- not
//     retried forever either, since retrying an inherently invalid payload
//     can never succeed.
//   - a thrown fetch (genuine network failure, not a server response at
//     all) -> left 'pending', and the whole replay loop stops immediately
//     (if we're still offline, every remaining entry would fail the same
//     way -- no point burning through them one at a time).
//
// Returns {synced: [client_sale_id,...], needsReauth: [...], failed: [...], stillOffline: bool}.
function posReplayQueue() {
    var result = { synced: [], needsReauth: [], failed: [], stillOffline: false };
    return posQueueList().then(function (entries) {
        var pending = entries.filter(function (e) { return e.status === 'pending'; });
        var chain = Promise.resolve();
        pending.forEach(function (entry) {
            chain = chain.then(function () {
                if (result.stillOffline) return; // a prior entry already proved we're offline
                return posGetCsrfToken().then(function (csrfToken) {
                    return fetch('/api/v1/sales/', {
                        method: 'POST',
                        headers: Object.assign(
                            { 'Content-Type': 'application/json' },
                            csrfToken ? { 'X-CSRFToken': csrfToken } : {}
                        ),
                        body: JSON.stringify(entry.payload),
                    }).then(function (response) {
                        if (response.ok) {
                            result.synced.push(entry.client_sale_id);
                            return posQueueRemove(entry.client_sale_id);
                        }
                        if (response.status === 401 || response.status === 403) {
                            result.needsReauth.push(entry.client_sale_id);
                            return posQueueUpdate(entry.client_sale_id, {
                                status: 'needsReauth', lastAttemptAt: new Date().toISOString(),
                            });
                        }
                        return response.json().catch(function () { return {}; }).then(function (data) {
                            result.failed.push(entry.client_sale_id);
                            var message = data.message || 'Could not sync this sale.';
                            var wasAlreadyFailed = entry.status === 'failed';
                            return posQueueUpdate(entry.client_sale_id, {
                                status: 'failed', lastAttemptAt: new Date().toISOString(), lastError: message,
                            }).then(function () {
                                // Only alert on the FIRST time this entry fails, not
                                // every subsequent retry attempt -- otherwise a
                                // staff member tapping "Retry now" repeatedly would
                                // spam the same Telegram alert each time.
                                if (!wasAlreadyFailed) return posAlertQueueFailure(entry.client_sale_id, entry.payload, message);
                            });
                        });
                    }, function () {
                        result.stillOffline = true; // fetch() itself rejected -- no connectivity
                    });
                });
            });
        });
        return chain.then(function () { return result; });
    });
}
