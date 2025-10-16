from rest_framework.permissions import BasePermission
from .models import StaffRole

class IsOwnerOrManager(BasePermission):
    def has_permission(self, request, view):
        u = getattr(request, "user", None)
        return bool(u and getattr(u, "role", None) in (StaffRole.OWNER, StaffRole.MANAGER))
