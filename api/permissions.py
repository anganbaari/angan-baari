import secrets

from django.conf import settings
from rest_framework.permissions import BasePermission


class IsStaffUser(BasePermission):
    """Same gate as @staff_member_required on the existing POS view —
    session-authenticated staff account, not a token."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)


class IsOwnerOrManager(BasePermission):
    """Staff-only AND restricted to the owner/manager roles — the Reports
    dashboard shows cash/credit/customer data a cashier-level staff
    account has no business reason to see at a till. Reuses the Phase A
    `UserProfile.role` field (admin/manager/cashier), which until now
    existed but was never actually consumed by any permission check
    anywhere in the codebase — this is its first real use. A superuser
    always passes, same convention as Django's own staff/perm checks,
    so an account created via createsuperuser isn't locked out just for
    lacking a UserProfile row."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_staff):
            return False
        if user.is_superuser:
            return True
        profile = getattr(user, 'profile', None)
        return bool(profile and profile.role in ('admin', 'manager'))


class IsRudraToken(BasePermission):
    """Gate for /api/rudra/ -- the owner-only Rudra assistant (a separate
    program on the owner's own PC), NOT a browser user. Authenticates with
    a single shared secret sent as `Authorization: Bearer <token>`, compared
    in constant time against settings.RUDRA_API_TOKEN (env var
    RUDRA_API_TOKEN).

    Deliberately independent of everything else: no Django user, no session,
    no DRF authtoken row (so no model/migration), and not interchangeable
    with the staff-session /api/v1/ gate (IsOwnerOrManager) or any future
    public-assistant (Saumya) credential -- each of those gets its own
    token/permission class so one leaking can't open the others.

    FAILS CLOSED: if RUDRA_API_TOKEN is unset or shorter than
    MIN_TOKEN_LENGTH, every request is denied. Views using this class must
    set authentication_classes = [] so DRF's default TokenAuthentication
    doesn't also run."""

    MIN_TOKEN_LENGTH = 32

    def has_permission(self, request, view):
        expected = getattr(settings, 'RUDRA_API_TOKEN', '') or ''
        if len(expected) < self.MIN_TOKEN_LENGTH:
            return False
        header = request.META.get('HTTP_AUTHORIZATION', '')
        scheme, _, supplied = header.partition(' ')
        if scheme.lower() != 'bearer' or not supplied:
            return False
        return secrets.compare_digest(supplied.strip().encode('utf-8'), expected.encode('utf-8'))
