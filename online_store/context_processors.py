from datetime import timedelta

from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Cart, Notification, Order, Paymentdetails, ProductAudit, Stock, StoreDetails


def store_context(request):
    cart_count = 0
    unread_notifications = 0
    nav_badges = {}
    user = getattr(request, 'user', None)

    if user and user.is_authenticated:
        unread_qs = Notification.objects.filter(recipient=user, is_read=False)
        unread_notifications = unread_qs.count()

        if getattr(user, 'is_wholesaler', False):
            stores_needing_setup = (
                StoreDetails.objects.filter(owner=user)
                .annotate(
                    active_areas=Count('service_areas', filter=Q(service_areas__is_active=True), distinct=True),
                    stock_cnt=Count('stocks', distinct=True),
                )
                .filter(Q(active_areas=0) | Q(stock_cnt=0))
                .count()
            )
            low_stock_count = Stock.objects.filter(
                store__owner=user,
                available_stock_count__lte=F('low_stock_threshold'),
            ).count()
            audit_count = ProductAudit.objects.filter(
                product__store__owner=user,
                reason__in=[
                    ProductAudit.ReasonClass.DAMAGED_GOODS,
                    ProductAudit.ReasonClass.DISCREPANCY_CORRECTION,
                ],
                timestamp__gte=timezone.now() - timedelta(days=7),
            ).count()
            active_orders_count = Order.objects.filter(
                store__owner=user,
                status__in=[
                    Order.OrderStatus.PENDING,
                    Order.OrderStatus.CONFIRMED,
                    Order.OrderStatus.PACKED,
                    Order.OrderStatus.SHIPPED,
                    Order.OrderStatus.IN_TRANSIT,
                    Order.OrderStatus.OUT_FOR_DELIVERY,
                ],
            ).count()
            pending_payment_qs = Paymentdetails.objects.filter(
                order__store__owner=user,
                status=Paymentdetails.VerificationStatus.PENDING,
            )
            pending_verifications = pending_payment_qs.count()
            pending_payment_order_ids = pending_payment_qs.values_list('order_id', flat=True)
            unpaid_without_pending = (
                Order.objects.filter(store__owner=user)
                .exclude(status=Order.OrderStatus.CANCELLED)
                .exclude(id__in=pending_payment_order_ids)
                .annotate(due=F('total_price') + F('delivery_fee') - F('amount_paid'))
                .filter(due__gt=0)
                .count()
            )
            payments_count = pending_verifications + unpaid_without_pending
            profile_incomplete = 1 if (not user.business_name or not user.city or not user.state) else 0

            nav_badges = {
                'dashboard': active_orders_count + payments_count + low_stock_count + unread_notifications,
                'stores': stores_needing_setup,
                'inventory': low_stock_count,
                'audit': audit_count,
                'orders': active_orders_count,
                'payments': payments_count,
                'alerts': unread_notifications,
                'profile': profile_incomplete,
            }

        elif getattr(user, 'is_retailer', False):
            cart = Cart.objects.filter(retailer=user).first()
            if cart:
                cart_count = cart.total_items

            stock_alerts = unread_qs.filter(product__isnull=False).count()
            retailer_orders = Order.objects.filter(customer=user)
            open_orders_count = retailer_orders.exclude(
                status__in=[Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED]
            ).count()
            awaiting_confirm_count = retailer_orders.filter(
                status__in=[Order.OrderStatus.DELIVERED, Order.OrderStatus.OUT_FOR_DELIVERY]
            ).count()
            unpaid_orders_count = (
                retailer_orders.exclude(status=Order.OrderStatus.CANCELLED)
                .annotate(due=F('total_price') + F('delivery_fee') - F('amount_paid'))
                .filter(due__gt=0)
                .count()
            )
            profile_incomplete = 1 if (not user.city or not user.state) else 0

            nav_badges = {
                'dashboard': awaiting_confirm_count + unpaid_orders_count + unread_notifications,
                'shops': stock_alerts,
                'catalog': stock_alerts,
                'cart': cart_count,
                'orders': open_orders_count,
                'payments': unpaid_orders_count,
                'alerts': unread_notifications,
                'profile': profile_incomplete,
            }

    return {
        'cart_item_count': cart_count,
        'unread_notification_count': unread_notifications,
        'nav_badges': nav_badges,
    }
