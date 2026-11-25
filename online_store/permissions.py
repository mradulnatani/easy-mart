from rest_framework.permissions import BasePermission


class IsWholesaler(BasePermission):
    message = 'Only Wholesaler accounts can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'is_wholesaler', False)
        )


class IsRetailer(BasePermission):
    message = 'Only Retailer accounts can perform this action.'

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, 'is_retailer', False)
        )


class IsStoreOwner(BasePermission):
    message = 'You do not own this store.'

    def has_object_permission(self, request, view, obj):
        store = getattr(obj, 'store', obj)
        owner = getattr(store, 'owner', None)
        return owner == request.user
