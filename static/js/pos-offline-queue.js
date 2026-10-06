// Shared IndexedDB-backed offline queue for POS sale submission.
//
// Loaded two ways, deliberately a classic (non-module) script so both work
// unchanged against the exact same file:
//   - <script src="..."> in templates/pos.html (page context -- functions
//     land as plain globals, i.e. window.posQueueEnqueue etc.)
//   - importScripts(...) inside static/js/pos-sw.js (service worker context
//     -- same functions land as globals on the worker's `self`)
//
// This queue exists so a sale that fails to reach POST /api/v1/sales/
// because of a NETWORK failure (not a real validation error) isn't lost --
// it's retried later, in order, relying on client_sale_id idempotency to
// make a double-send harmless. The same database also holds a small `meta`
// store (Phase 2) recording when /pos/ was last successfully cached, for
// the "showing data as of ..." staleness banner in pos.html, and (Phase 3)
// an `offlineOperators` store letting PIN unlock work without a network
// connection -- see posStoreOfflineOperator()/posVerifyOfflineOperator()
// below.

var POS_QUEUE_DB_NAME = 'angan-baari-pos';
var POS_QUEUE_DB_VERSION = 3;
var POS_QUEUE_STORE = 'queuedSales';

// The Cache Storage bucket holding the cached /pos/ shell and its
// precached /static/ dependencies. Declared here (not in pos-sw.js) so
// pos.html's own posSelfCachePosPage() and pos-sw.js's fetch handler are
// guaranteed to agree on the exact same cache name -- both load this file
// (a <script> tag in pos.html, importScripts() in pos-sw.js), so one
// declaration here reaches both contexts instead of needing to be kept in
// sync as two separate literals.
//
// BUMP THIS whenever a deploy changes anything the shell cache holds the
// shape/meaning of -- the /pos/ page structure, what's embedded in it, or
// which /static/ assets it depends on. pos-sw.js's `activate` handler
// deletes every OTHER 'pos-shell-*' cache on each activation, so bumping
// this is what actually forces every device to drop its old cached shell
// and rebuild from a fresh network load, rather than serving a
// structurally-stale cached page forever. It is NOT bumped automatically
// by a deploy -- there's no build step in this project to hook that into
// (see CLAUDE.md's Stack section), so this is a manual, deliberate step,
// same spirit as the "cache-bust static assets with ?v=N" convention
// already used for CSS/JS elsewhere.
var POS_SHELL_CACHE = 'pos-shell-v3';
var POS_META_STORE = 'meta';
var POS_OFFLINE_PIN_STORE = 'offlineOperators';

function posQueueOpenDB() {
    return new Promise(function (resolve, reject) {
        var req = indexedDB.open(POS_QUEUE_DB_NAME, POS_QUEUE_DB_VERSION);
        req.onupgradeneeded = function () {
            var db = req.result;
            if (!db.objectStoreNames.contains(POS_QUEUE_STORE)) {
                db.createObjectStore(POS_QUEUE_STORE, { keyPath: 'client_sale_id' });
            }
            if (!db.objectStoreNames.contains(POS_META_STORE)) {
                db.createObjectStore(POS_META_STORE, { keyPath: 'key' });
            }
            if (!db.objectStoreNames.contains(POS_OFFLINE_PIN_STORE)) {
                db.createObjectStore(POS_OFFLINE_PIN_STORE, { keyPath: 'operator_id' });
            }
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
    });
}

// Small key/value store shared by the page and the service worker --
// currently just 'lastCachedAt' (ISO timestamp), written by pos-sw.js every
// time it successfully caches a fresh /pos/ response, read by pos.html to
// show "Offline -- showing data as of {that timestamp}" whenever
// navigator.onLine is false. Not folded into localStorage because a
// service worker has no access to it at all.
function posSetMeta(key, value) {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_META_STORE, 'readwrite');
            tx.objectStore(POS_META_STORE).put({ key: key, value: value });
            tx.oncomplete = function () { resolve(); };
            tx.onerror = function () { reject(tx.error); };
        });
    });
}

function posGetMeta(key) {
    return posQueueOpenDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            var tx = db.transaction(POS_META_STORE, 'readonly');
            var req = tx.objectStore(POS_META_STORE).get(key);
            req.onsuccess = function () { resolve(req.result ? req.result.value : null); };
            req.onerror = function () { reject(req.error); };
        });
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
                status: 'pending', // 'pending' | 'needsReauth' | 'stockConflict' | 'failed'
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
//   - 409 (stock genuinely ran out while offline) -> marked 'stockConflict',
//     NOT 'failed' and NOT Telegram-alerted. This is an expected, routine
//     outcome of offline operation (someone else sold the last one before
//     this queued sale replayed), not a bug -- it shouldn't look the same
//     to staff, or to whoever reads the failure-alert Telegram channel, as
//     a malformed payload that can never succeed. The 409 message already
//     names which product/variant is short (see InventoryMovement.clean()
//     in shop/models.py) so staff know which line item to deal with.
//   - any other 4xx (a real validation bug, not a stock conflict) -> marked
//     'failed' and left in the queue for staff to review/dismiss, with one
//     Telegram alert -- not retried forever either, since retrying an
//     inherently invalid payload can never succeed.
//   - a thrown fetch (genuine network failure, not a server response at
//     all) -> left 'pending', and the whole replay loop stops immediately
//     (if we're still offline, every remaining entry would fail the same
//     way -- no point burning through them one at a time).
//
// Returns {synced, needsReauth, stockConflict, failed: [client_sale_id,...], stillOffline: bool}.
function posReplayQueue() {
    var result = { synced: [], needsReauth: [], stockConflict: [], failed: [], stillOffline: false };
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
                            var message = data.message || 'Could not sync this sale.';
                            if (response.status === 409) {
                                result.stockConflict.push(entry.client_sale_id);
                                return posQueueUpdate(entry.client_sale_id, {
                                    status: 'stockConflict', lastAttemptAt: new Date().toISOString(), lastError: message,
                                });
                            }
                            result.failed.push(entry.client_sale_id);
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

// ══════════════════════════════════════════════════════════════
// Offline PIN unlock (Phase 3).
//
// PosUnlockView (api/views.py) is the real check -- this is only a local
// fallback for when that request can't reach the server at all, so the
// till isn't permanently stuck on the lock screen for the rest of a
// connectivity outage. posStoreOfflineOperator() is called once, right
// after a SUCCESSFUL online unlock (see submitPin() in pos.html), caching
// a PBKDF2-SHA256 hash of that PIN under a per-operator random salt.
// posVerifyOfflineOperator() is what a failed-to-reach-server unlock
// attempt falls back to.
//
// Deliberate, documented tradeoff (see CLAUDE.md's POS-PWA section): a
// 4-digit PIN is only 10,000 combinations, and PBKDF2's iteration count
// cannot fix that -- an attacker with the stolen device can read this
// hash directly out of IndexedDB and brute-force all 10,000 guesses in
// well under a minute even at a deliberately slow iteration count. The
// iteration count below is still set to a real, current PBKDF2-SHA256
// minimum (not left at some trivial default) because it costs a
// legitimate unlock nothing noticeable, but it must not be mistaken for
// real protection -- the actual boundary this relies on is the same one
// the server-side PIN already relies on (CLAUDE.md, UserProfile.set_pin):
// this identifies who is operating an already-access-controlled terminal,
// it was never the terminal's own access control (that's still the
// underlying is_staff Django session, unaffected by any of this). The
// lockout below is a deterrent against a casual guesser, not a dedicated
// attacker -- anyone who can read this store directly can also clear or
// edit the lockout counter sitting right next to it.
var POS_OFFLINE_PIN_ITERATIONS = 210000; // current (2020s) PBKDF2-SHA256 floor -- see tradeoff note above
var POS_OFFLINE_PIN_MAX_ATTEMPTS = 5;
var POS_OFFLINE_PIN_LOCKOUT_MS = 5 * 60 * 1000; // mirrors PosUnlockView's own window
var POS_OFFLINE_PIN_EXPIRY_MS = 7 * 24 * 60 * 60 * 1000; // must re-unlock online at least weekly

function posHexFromBuffer(buf) {
    return Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');
}

function posBufferFromHex(hex) {
    var bytes = new Uint8Array(hex.length / 2);
    for (var i = 0; i < bytes.length; i++) bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
    return bytes;
}

function posGenerateSaltHex() {
    return posHexFromBuffer(crypto.getRandomValues(new Uint8Array(16)).buffer);
}

function posPbkdf2HashHex(pin, saltHex, iterations) {
    var enc = new TextEncoder();
    return crypto.subtle.importKey('raw', enc.encode(pin), { name: 'PBKDF2' }, false, ['deriveBits'])
        .then(function (keyMaterial) {
            return crypto.subtle.deriveBits(
                { name: 'PBKDF2', salt: posBufferFromHex(saltHex), iterations: iterations, hash: 'SHA-256' },
                keyMaterial, 256
            );
        })
        .then(function (bits) { return posHexFromBuffer(bits); });
}

// operator: { id, name, role } -- the exact shape PosUnlockView already
// returns on success, so the offline path can set currentOperator from
// either source without the rest of pos.html needing to know which one
// ran. Re-storing on every successful ONLINE unlock (not just the first
// time) is what satisfies "must be refreshed online after N days" --
// routine daily use naturally keeps resetting the expiry clock.
function posStoreOfflineOperator(operator, pin) {
    var salt = posGenerateSaltHex();
    return posPbkdf2HashHex(pin, salt, POS_OFFLINE_PIN_ITERATIONS).then(function (hash) {
        return posQueueOpenDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var tx = db.transaction(POS_OFFLINE_PIN_STORE, 'readwrite');
                var store = tx.objectStore(POS_OFFLINE_PIN_STORE);
                store.put({
                    operator_id: operator.id, name: operator.name, role: operator.role,
                    salt: salt, hash: hash, iterations: POS_OFFLINE_PIN_ITERATIONS,
                    createdAt: new Date().toISOString(),
                });
                // Opportunistic cleanup -- drop any OTHER cached operator whose
                // entry has already aged past the expiry window, so stale
                // records don't accumulate forever as staff come and go. Only
                // runs when a fresh entry is being written, which is enough:
                // an operator who never unlocks online again also never
                // offline-unlocks again past their own expiry anyway.
                var cutoff = Date.now() - POS_OFFLINE_PIN_EXPIRY_MS;
                var cursorReq = store.openCursor();
                cursorReq.onsuccess = function () {
                    var cursor = cursorReq.result;
                    if (!cursor) return;
                    if (new Date(cursor.value.createdAt).getTime() < cutoff) cursor.delete();
                    cursor.continue();
                };
                tx.oncomplete = function () { resolve(); };
                tx.onerror = function () { reject(tx.error); };
            });
        });
    });
}

// Returns one of:
//   { ok: true, operator: {id, name, role} }
//   { ok: false, reason: 'locked_out', retryAfterSeconds }
//   { ok: false, reason: 'invalid_pin', attemptsRemaining }
//   { ok: false, reason: 'expired' }           -- cached PIN(s) exist but are too old
//   { ok: false, reason: 'no_cached_operators' } -- this device has never unlocked online
//
// The failed-attempt lockout is device-wide, not per-operator -- same
// reasoning PosUnlockView's own docstring already gives for its session-
// scoped (not per-user) lockout: it's throttling guesses at THIS
// terminal, not tracking any one staff account.
function posVerifyOfflineOperator(pin) {
    return posGetMeta('offlineLockoutUntil').then(function (lockoutUntil) {
        if (lockoutUntil && Date.now() < lockoutUntil) {
            return { ok: false, reason: 'locked_out', retryAfterSeconds: Math.ceil((lockoutUntil - Date.now()) / 1000) };
        }
        return posQueueOpenDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var tx = db.transaction(POS_OFFLINE_PIN_STORE, 'readonly');
                var req = tx.objectStore(POS_OFFLINE_PIN_STORE).getAll();
                req.onsuccess = function () { resolve(req.result); };
                req.onerror = function () { reject(req.error); };
            });
        }).then(function (records) {
            if (!records.length) return { ok: false, reason: 'no_cached_operators' };
            var cutoff = Date.now() - POS_OFFLINE_PIN_EXPIRY_MS;
            var fresh = records.filter(function (r) { return new Date(r.createdAt).getTime() >= cutoff; });
            if (!fresh.length) return { ok: false, reason: 'expired' };
            return Promise.all(fresh.map(function (r) {
                return posPbkdf2HashHex(pin, r.salt, r.iterations).then(function (hash) {
                    return hash === r.hash ? r : null;
                });
            })).then(function (results) {
                var match = results.find(function (r) { return r; });
                if (match) {
                    return posSetMeta('offlineFailedAttempts', 0).then(function () {
                        return { ok: true, operator: { id: match.operator_id, name: match.name, role: match.role } };
                    });
                }
                return posGetMeta('offlineFailedAttempts').then(function (attempts) {
                    attempts = (attempts || 0) + 1;
                    if (attempts >= POS_OFFLINE_PIN_MAX_ATTEMPTS) {
                        var until = Date.now() + POS_OFFLINE_PIN_LOCKOUT_MS;
                        return Promise.all([
                            posSetMeta('offlineFailedAttempts', 0),
                            posSetMeta('offlineLockoutUntil', until),
                        ]).then(function () {
                            return { ok: false, reason: 'locked_out', retryAfterSeconds: Math.ceil(POS_OFFLINE_PIN_LOCKOUT_MS / 1000) };
                        });
                    }
                    return posSetMeta('offlineFailedAttempts', attempts).then(function () {
                        return { ok: false, reason: 'invalid_pin', attemptsRemaining: POS_OFFLINE_PIN_MAX_ATTEMPTS - attempts };
                    });
                });
            });
        });
    });
}
