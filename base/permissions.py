from rest_framework.permissions import BasePermission


def is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or getattr(getattr(user, 'profile', None), 'role', '') == 'admin'))


class IsAdmin(BasePermission):
    """Admin (the manager). Mirrors SRC's IsAdminUser."""
    message = 'Admins only.'

    def has_permission(self, request, view):
        return is_admin_user(request.user)
