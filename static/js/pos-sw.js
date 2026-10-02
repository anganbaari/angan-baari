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
// Phase 1 scope only (see CLAUDE.md / the POS-PWA roadmap): this worker
// exists to (a) make /pos/ installable and (b) run Background Sync so a
// sale queued while offline (see static/js/pos-offline-queue.js) can
// replay even if the POS tab isn't focused or open. It does NOT cache the
// catalog or any page assets for cold-start offline use -- that's Phase 2.

importScripts('/static/js/pos-offline-queue.js');

self.addEventListener('install', function (event) {
    self.skipWaiting(); // don't make staff wait through an update prompt on a shared till
});

self.addEventListener('activate', function (event) {
    event.waitUntil(self.clients.claim());
});

// A plain network pass-through -- no caching here (Phase 2). Some browsers'
// installability checks still look for the presence of a fetch handler,
// so this exists even though it does nothing beyond what the network
// would do unhandled.
self.addEventListener('fetch', function (event) {
    event.respondWith(fetch(event.request));
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
