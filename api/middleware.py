from django.conf import settings
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

ALLOWED_METHODS = 'GET, HEAD, OPTIONS'


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
    restriction (GET/HEAD/OPTIONS only — the write endpoints,
    /api/v1/sales/ and /api/v1/orders/, aren't called from Next.js yet).

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

        is_preflight = (
            is_allowed
            and request.method == 'OPTIONS'
            and 'HTTP_ACCESS_CONTROL_REQUEST_METHOD' in request.META
        )
        if is_preflight:
            response = HttpResponse(status=204)
            response['Access-Control-Allow-Origin'] = origin
            response['Access-Control-Allow-Methods'] = ALLOWED_METHODS
            requested_headers = request.META.get('HTTP_ACCESS_CONTROL_REQUEST_HEADERS')
            if requested_headers:
                response['Access-Control-Allow-Headers'] = requested_headers
            response['Access-Control-Max-Age'] = '86400'
            patch_vary_headers(response, ['Origin'])
            return response

        response = self.get_response(request)

        if is_allowed and request.method in ('GET', 'HEAD'):
            response['Access-Control-Allow-Origin'] = origin
            patch_vary_headers(response, ['Origin'])

        return response
