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
