from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

READ_METHODS = ('GET', 'HEAD')

# POST is allowed cross-origin only for these exact paths. Each one is
# either public/AllowAny (orders, signup, login, password-reset*) or
# authenticates itself via a per-request Authorization header rather than a
# cookie (logout) — none of them carry the session/CSRF-trust concerns
# /api/v1/sales/ (staff, SessionAuthentication) would, so that one stays
# GET-only cross-origin until it gets real cross-origin session support.
# Deliberately exact matches, not a prefix: /api/v1/orders/<id>/ (order
# status, GET-only) is untouched.
POST_ALLOWED_PATHS = {
    '/api/v1/orders/',
    '/api/v1/auth/signup/',
    '/api/v1/auth/login/',
    '/api/v1/auth/logout/',
    '/api/v1/auth/password-reset/',
    '/api/v1/auth/password-reset-confirm/',
}


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
    paths in POST_ALLOWED_PATHS above (guest orders, and now the token-auth
    endpoints). No session/CSRF cookies are involved anywhere in this
    middleware — the auth endpoints use a per-request Authorization header
    instead (see api/views.py's AUTH section), so the SESSION_COOKIE_*/
    CSRF_TRUSTED_ORIGINS changes drafted during Phase 2 stayed unnecessary
    and were never applied.

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
        allow_post = request.path in POST_ALLOWED_PATHS
        allowed_methods = 'GET, HEAD, OPTIONS, POST' if allow_post else 'GET, HEAD, OPTIONS'

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

        allowed_actual_methods = READ_METHODS + ('POST',) if allow_post else READ_METHODS
        if is_allowed and request.method in allowed_actual_methods:
            response['Access-Control-Allow-Origin'] = origin
            patch_vary_headers(response, ['Origin'])

        return response
