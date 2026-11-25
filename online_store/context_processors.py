from .models import Cart, Notification


def store_context(request):
    cart_count = 0
    unread_notifications = 0
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        unread_notifications = Notification.objects.filter(recipient=user, is_read=False).count()
        if getattr(user, 'is_retailer', False):
            cart = Cart.objects.filter(retailer=user).first()
            if cart:
                cart_count = cart.total_items
    return {
        'cart_item_count': cart_count,
        'unread_notification_count': unread_notifications,
    }
