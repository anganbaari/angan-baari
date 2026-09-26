from rest_framework.permissions import BasePermission


class IsStaffUser(BasePermission):
    """Same gate as @staff_member_required on the existing POS view —
    session-authenticated staff account, not a token."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
