import re

from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

READ_METHODS = ('GET', 'HEAD')

# POST is allowed cross-origin only for these exact paths. Each one is
# either public/AllowAny (orders, signup, login, password-reset*) or
# authenticates itself via a per-request Authorization header rather than a
# cookie (logout, and now the Phase 4 profile-page endpoints below) — none
# of them carry the session/CSRF-trust concerns /api/v1/sales/ (staff,
# SessionAuthentication) would, so that one stays GET-only cross-origin
# until it gets real cross-origin session support.
# Deliberately exact matches, not a prefix: /api/v1/orders/<id>/ (order
# status, GET-only) is untouched.
POST_ALLOWED_PATHS = {
    '/api/v1/orders/',
    '/api/v1/auth/signup/',
    '/api/v1/auth/login/',
    '/api/v1/auth/logout/',
    '/api/v1/auth/password-reset/',
    '/api/v1/auth/password-reset-confirm/',
    '/api/v1/wishlist/toggle/',
    '/api/v1/wishlist/set-variant/',
    '/api/v1/wishlist/move-to-cart/',
    '/api/v1/profile/change-password/',
}

# /api/v1/orders/<id>/cancel/ and /api/v1/orders/<id>/reorder/ carry a
# variable id, so they can't join the exact-match set above. Matched by a
# narrow regex each (not a blanket /api/v1/orders/ prefix), for the same
# reason the set above uses exact matches instead of a prefix:
# /api/v1/orders/<id>/ (status, GET-only) must not be swept in alongside
# them.
POST_ALLOWED_PATH_PATTERNS = (
    re.compile(r'^/api/v1/orders/\d+/cancel/$'),
    re.compile(r'^/api/v1/orders/\d+/reorder/$'),
)

# PATCH is allowed cross-origin only here — same per-request
# Authorization-header authentication as the POST-allowed paths above, no
# session/CSRF involved.
PATCH_ALLOWED_PATHS = {
    '/api/v1/profile/',
}


def _path_allows_post(path):
    return path in POST_ALLOWED_PATHS or any(pattern.match(path) for pattern in POST_ALLOWED_PATH_PATTERNS)


class ApiV1CorsMiddleware:
    """A narrow, separate CORS layer for the new /api/v1/ read API only —
    used by a Next.js frontend on its own domain. Deliberately NOT built by
    widening django-cors-headers' existing config (CORS_ALLOWED_ORIGINS /
    CORS_URLS_REGEX in settings.py), because that config is shared by every
    path it matches: widening it to cover /api/v1/ would also grant the new
    Next.js origin CORS access to /api/inventory/movements/ (the ABMS
    endpoint, including POST), and narrowing its CORS_URLS_REGEX to only
    /api/v1/ would stop it emitting CORS headers for ABMS's own real
    cross-origin POST there. So django-cors-headers is left completely
    untouched for /api/inventory/, and this middleware independently
    handles /api/v1/ with its own origin list and its own method
    restriction — GET/HEAD/OPTIONS everywhere, plus POST for the specific
    paths in POST_ALLOWED_PATHS/POST_ALLOWED_PATH_PATTERNS above (guest
    orders, the token-auth endpoints, and now the Phase 4 profile-page
    endpoints) and PATCH for PATCH_ALLOWED_PATHS. No session/CSRF cookies
    are involved anywhere in this middleware — the auth endpoints and the
    Phase 4 endpoints all use a per-request Authorization header instead
    (see api/views.py), so the SESSION_COOKIE_*/CSRF_TRUSTED_ORIGINS
    changes drafted during Phase 2 stayed unnecessary and were never
    applied.

    Origins come from settings.API_V1_CORS_ALLOWED_ORIGINS (env-driven, see
    farmsite/settings.py), so the real deployed Next.js domain can be added
    later without a code change.

    Like all CORS, this only controls whether a BROWSER lets foreign-origin
    JS read the response — it never blocks the request from reaching the
    view. A non-browser caller (curl, a mobile app, a future first-party
    client) can still call any /api/v1/ endpoint with any method exactly as
    before; nothing here changes server-side auth or permissions.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        origin = request.META.get('HTTP_ORIGIN')
        is_allowed = (
            request.path.startswith('/api/v1/')
            and origin in settings.API_V1_CORS_ALLOWED_ORIGINS
        )
        allow_post = _path_allows_post(request.path)
        allow_patch = request.path in PATCH_ALLOWED_PATHS

        allowed_methods_list = ['GET', 'HEAD', 'OPTIONS']
        if allow_post:
            allowed_methods_list.append('POST')
        if allow_patch:
            allowed_methods_list.append('PATCH')
        allowed_methods = ', '.join(allowed_methods_list)

        is_preflight = (
            is_allowed
            and request.method == 'OPTIONS'
            and 'HTTP_ACCESS_CONTROL_REQUEST_METHOD' in request.META
        )
        if is_preflight:
            response = HttpResponse(status=204)
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Methods'] = allowed_methods
            requested_headers = request.META.get('HTTP_ACCESS_CONTROL_REQUEST_HEADERS')
            if requested_headers:
                response['Access-Control-Allow-Headers'] = requested_headers
            response['Access-Control-Max-Age'] = '86400'
            patch_vary_headers(response, ['Origin'])
            return response

        response = self.get_response(request)

        allowed_actual_methods = list(READ_METHODS)
        if allow_post:
            allowed_actual_methods.append('POST')
        if allow_patch:
            allowed_actual_methods.append('PATCH')
        if is_allowed and request.method in allowed_actual_methods:
            response['Access-Control-Allow-Origin'] = origin
            patch_vary_headers(response, ['Origin'])

        return response
