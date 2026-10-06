// POS service worker -- scope "/pos/" only, registered from templates/pos.html.
//
// This file itself lives under /static/js/ (so it's an ordinary static
// asset, editable/versioned the normal way), but it's SERVED at
// /pos/service-worker.js by a small dedicated Django view
// (shop/views.py::pos_service_worker) instead of through Django's static
// files machinery. That's load-bearing, not a style choice: a service
// worker's maximum allowed scope defaults to the directory its OWN script
// URL lives in, unless the response carries a `Service-Worker-Allowed`
// header widening it. A file served from /static/js/ would default to
// scope /static/js/ -- nowhere near /pos/ -- and PythonAnywhere's static-
// files mapping (the Web tab UI) has no way to attach a custom response
// header to one specific static file. Serving it from a real Django view
// under /pos/ sidesteps the problem entirely (its natural scope already
// covers /pos/), and the view sets Service-Worker-Allowed: /pos/ anyway as
// a second line of defense.
//
// Phase 2 (this version): /pos/ itself can cold-start with zero
// connectivity, not just survive a connectivity drop mid-session (Phase 1).
// Two cache strategies, deliberately NOT a client-side-rendering rewrite --
// pos.html still gets PRODUCTS/CATEGORIES as server-rendered embedded JSON
// exactly as before, this just makes sure a COPY of that rendered page is
// always sitting in Cache Storage:
//   1. Navigating to /pos/ itself: network-first, caching every successful
//      response as the new "last known good" snapshot (including its
//      embedded posProductsData/posCategoriesData JSON verbatim), falling
//      back to that cached snapshot when the network fetch fails. This is
//      why a cold load with no connectivity at all still renders the POS
//      screen with the catalog as of the last time it loaded online --
//      there's no separate catalog cache to keep in sync, the cached PAGE
//      already contains whatever catalog data it was rendered with.
//   2. Same-origin /static/ assets (CSS/JS/icons) the cached page needs to
//      actually render correctly offline: stale-while-revalidate -- serve
//      the cached copy immediately if there is one (so a cold load doesn't
//      block on the network for these), refreshing the cache in the
//      background when online. Cross-origin assets (Google Fonts, Font
//      Awesome's CDN) are NOT cached -- they'll just fail to load offline,
//      same as any ordinary page; caching third-party CDN responses is a
//      bigger can of worms (versioning, opaque responses) than this phase
//      needs for "the product grid still works."
// API calls (/api/v1/..., /pos/service-worker.js) are never cached --
// they're either live session-bound data (stock, sales) that would be
// actively wrong to serve stale, or the worker's own script (the browser
// handles that caching/update lifecycle itself).

importScripts('/static/js/pos-offline-queue.js');

// POS_SHELL_CACHE itself is declared in pos-offline-queue.js, not here --
// pos.html needs the exact same cache name too (see posSelfCachePosPage()
// there), and that file is the one already loaded in both contexts
// (importScripts() here, a plain <script> tag in pos.html), so declaring
// it there once is what keeps both sides using the identical bucket
// without having to hand-sync a literal string in two files.

// Every same-origin /static/ file pos.html actually needs to RENDER
// correctly, precached explicitly here rather than left to the
// stale-while-revalidate branch below. That branch only ever catches a
// request if the SW is already controlling the page making it -- and per
// spec, a page's own navigation (the very first /pos/ load after this SW
// registers) is NEVER controlled by the SW it just registered, so none of
// these would otherwise get cached until a SECOND load happens to request
// them again while the SW has since finished activating. In practice that
// second load usually wins the race fast enough on a quick local reload,
// but on a slow connection (or PythonAnywhere's free-tier cold start) the
// window where it DOESN'T is wide enough to matter -- confirmed by testing
// a genuinely clean profile: a single load with no reload left Cache
// Storage completely empty. Precaching here removes that race entirely.
// /pos/ itself is deliberately NOT listed -- it needs an authenticated
// session to mean anything, and precache's plain fetch() (unlike the
// navigation handler below) follows redirects transparently, which would
// risk caching a login-redirect page under the '/pos/' key during install.
// Caching the real /pos/ response is instead handled by pos.html itself,
// immediately after every successful load it renders (see posSelfCache()
// in pos.html) -- that runs in the PAGE's own context, so it isn't subject
// to the "can't control its own navigation" limitation at all.
var POS_PRECACHE_URLS = [
    '/static/style.css',
    '/static/js/pos-offline-queue.js',
    '/static/pos-manifest.json',
    '/static/images/logo-icon.png',
    '/static/images/pos-icon-192.png',
    '/static/images/pos-icon-512.png',
    '/static/images/pos-icon-192-maskable.png',
    '/static/images/pos-icon-512-maskable.png',
];

self.addEventListener('install', function (event) {
    // Skip the normal "new SW waits until every tab using the old one
    // closes" phase -- a shared till only ever has one tab open, so there's
    // nothing to wait FOR, and waiting would just mean staff never get the
    // update until someone thinks to fully close and reopen the browser.
    self.skipWaiting();
    event.waitUntil(
        caches.open(POS_SHELL_CACHE).then(function (cache) {
            // Each URL caught independently (not cache.addAll(), which
            // aborts the ENTIRE batch if even one request fails) -- one
            // missing/renamed asset shouldn't cost every other one its
            // precache entry.
            return Promise.all(POS_PRECACHE_URLS.map(function (url) {
                return cache.add(url).catch(function (err) {
                    console.warn('POS precache failed for', url, err);
                });
            }));
        })
    );
});

self.addEventListener('activate', function (event) {
    event.waitUntil(
        Promise.all([
            // Take control of any already-open /pos/ tab immediately,
            // without it needing to navigate again first. Combined with
            // skipWaiting() above, a new SW version goes from "deployed" to
            // "fully active and controlling the open tab" as soon as the
            // browser notices the updated pos-sw.js bytes -- normally on
            // this tab's next navigation/reload, since that's when browsers
            // check a registered SW's script for changes. See the
            // controllerchange listener in pos.html for why the page itself
            // then forces a reload the moment that handover happens: without
            // it, the OLD page JS (pos-offline-queue.js loaded once at page
            // load, never live-updated) would keep running while the NEW SW
            // is already active underneath it, which matters most if a
            // future version ever bumps the IndexedDB schema (POS_QUEUE_DB_VERSION
            // in pos-offline-queue.js) -- IndexedDB only allows opening a
            // database at the SAME OR HIGHER version it's already at, so the
            // old page's still-loaded, lower-version code would start
            // failing every queue operation until it reloads anyway. Forcing
            // the reload ourselves, right when the handover happens, means
            // staff see one unprompted screen refresh instead of a broken
            // queue.
            self.clients.claim(),
            caches.keys().then(function (names) {
                return Promise.all(
                    names
                        .filter(function (name) { return name.indexOf('pos-shell-') === 0 && name !== POS_SHELL_CACHE; })
                        .map(function (name) { return caches.delete(name); })
                );
            }),
        ])
    );
});

function posIsNavigationToPosScreen(request) {
    if (request.mode !== 'navigate') return false;
    var url = new URL(request.url);
    return url.pathname === '/pos/';
}

function posIsCacheableStaticAsset(request) {
    var url = new URL(request.url);
    return url.origin === self.location.origin && url.pathname.indexOf('/static/') === 0;
}

self.addEventListener('fetch', function (event) {
    var request = event.request;

    if (posIsNavigationToPosScreen(request)) {
        event.respondWith(
            fetch(request).then(function (response) {
                // A navigation's `event.request` has an implicit
                // redirect:'manual' mode, so if the server redirects (e.g.
                // @staff_member_required bouncing an expired session to
                // /admin/login/), this `response` is an OPAQUE redirect --
                // status 0, empty, unreadable body -- not the login page's
                // content and definitely not /pos/'s. The browser still
                // follows it correctly on its own for what the user
                // actually sees (confirmed by testing), but caching this
                // opaque response under the '/pos/' key would silently
                // replace the last known GOOD cached page with a useless
                // blank one -- worse than doing nothing, since the NEXT
                // offline load would then render blank instead of falling
                // back to genuinely stale-but-real content. response.ok is
                // checked too, for the same reason: don't let a transient
                // 500 overwrite a perfectly good existing cache entry.
                if (response.type !== 'opaqueredirect' && response.ok) {
                    var copy = response.clone();
                    caches.open(POS_SHELL_CACHE).then(function (cache) { cache.put(request, copy); });
                    posSetMeta('lastCachedAt', new Date().toISOString());
                }
                return response;
            }).catch(function () {
                return caches.match(request).then(function (cached) {
                    if (!cached) {
                        return new Response(
                            'Offline, and no cached copy of /pos/ is available yet -- load it once while online first.',
                            { status: 503, headers: { 'Content-Type': 'text/plain' } }
                        );
                    }
                    // Mark the served HTML so the page itself can tell, at
                    // its OWN init time, that THIS load came from the
                    // offline fallback rather than a fresh network response
                    // -- navigator.onLine alone can't answer that (it only
                    // reflects whether the network interface is up, not
                    // whether this specific request actually reached the
                    // server), and there's no direct JS API for a page to
                    // read its own navigation response's headers after the
                    // fact. See updateOfflineBanner() in pos.html.
                    return cached.text().then(function (html) {
                        var marked = html.replace('<head>', '<head><meta name="pos-cache-fallback" content="true">');
                        return new Response(marked, {
                            status: cached.status, statusText: cached.statusText, headers: cached.headers,
                        });
                    });
                });
            })
        );
        return;
    }

    if (posIsCacheableStaticAsset(request)) {
        event.respondWith(
            caches.open(POS_SHELL_CACHE).then(function (cache) {
                return cache.match(request).then(function (cached) {
                    var networkFetch = fetch(request).then(function (response) {
                        cache.put(request, response.clone());
                        return response;
                    }).catch(function () { return cached; }); // offline and nothing cached -- let it fail naturally
                    return cached || networkFetch;
                });
            })
        );
        return;
    }

    // Everything else (API calls, cross-origin CDN assets): plain
    // pass-through, same as Phase 1 -- never cached.
    event.respondWith(fetch(request));
});

function posBroadcastQueueResult(result) {
    return self.clients.matchAll({ includeUncontrolled: true }).then(function (clientList) {
        clientList.forEach(function (client) {
            client.postMessage({ type: 'POS_QUEUE_SYNCED', result: result });
        });
    });
}

// Background Sync -- the tag name is registered from pos.html when a sale
// gets queued (see registerPosBackgroundSync() there). Not supported in
// Safari/iOS at all, so this event simply never fires there -- pos.html's
// own page-load/online/visibilitychange replay calls are the fallback for
// browsers that never fire this event, page-load specifically being the
// one iOS can't avoid giving a chance to run (reopening an installed PWA
// from the home screen is a fresh load, not necessarily an online/
// visibilitychange transition).
self.addEventListener('sync', function (event) {
    if (event.tag === 'pos-replay-queue') {
        event.waitUntil(posReplayQueue().then(posBroadcastQueueResult));
    }
});
