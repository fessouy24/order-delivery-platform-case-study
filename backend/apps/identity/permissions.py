from django.conf import settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

ROLES = (
    "Admin",
    "Management",
    "Sales",
    "Supply Chain",
    "Warehouse",
    "Courier",
    "Finance",
    "Production",
)


def roles(user):
    if not user.is_authenticated or not user.is_active:
        return set()
    if user.is_superuser:
        return {"Admin"}
    return set(user.groups.values_list("name", flat=True)) & set(ROLES)


def allowed(user, *names):
    current = roles(user)
    return "Admin" in current or bool(current & set(names))


def require_role(user, *names):
    if not allowed(user, *names):
        raise PermissionDenied("Your role does not allow this action.")


class VerifiedUser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user.is_authenticated or not user.is_active:
            return False
        privileged = user.is_staff or allowed(user, "Admin")
        return (
            not (settings.REQUIRE_STAFF_MFA and privileged)
            or getattr(user, "is_verified", lambda: False)()
        )
