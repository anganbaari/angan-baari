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

// BUMP THIS whenever a deploy changes anything the shell cache holds the
// shape/meaning of -- the /pos/ page structure, what's embedded in it, or
// which /static/ assets it depends on. The `activate` handler below
// deletes every OTHER 'pos-shell-*' cache on each activation, so bumping
// this is what actually forces every device to drop its old cached shell
// and rebuild from a fresh network load, rather than serving a
// structurally-stale cached page forever. It is NOT bumped automatically
// by a deploy -- there's no build step in this project to hook that into
// (see CLAUDE.md's Stack section), so this is a manual, deliberate step,
// same spirit as the "cache-bust static assets with ?v=N" convention
// already used for CSS/JS elsewhere.
var POS_SHELL_CACHE = 'pos-shell-v2';

self.addEventListener('install', function (event) {
    // Skip the normal "new SW waits until every tab using the old one
    // closes" phase -- a shared till only ever has one tab open, so there's
    // nothing to wait FOR, and waiting would just mean staff never get the
    // update until someone thinks to fully close and reopen the browser.
    self.skipWaiting();
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
                var copy = response.clone();
                caches.open(POS_SHELL_CACHE).then(function (cache) { cache.put(request, copy); });
                posSetMeta('lastCachedAt', new Date().toISOString());
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
