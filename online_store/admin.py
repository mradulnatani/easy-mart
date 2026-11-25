from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    Cart,
    CartItem,
    CustomUser,
    Notification,
    Order,
    OrderItem,
    OrderStatusLog,
    Paymentdetails,
    ProductAudit,
    ProductReview,
    ServiceArea,
    Shipment,
    Stock,
    StoreDetails,
)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'role', 'business_name', 'city', 'state', 'is_active')
    list_filter = ('role', 'state', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('B2B Details', {
            'fields': (
                'role', 'business_name', 'gst_number', 'phone_number',
                'shipping_address', 'city', 'state', 'pin_code', 'credit_limit',
            )
        }),
    )


@admin.register(StoreDetails)
class StoreDetailsAdmin(admin.ModelAdmin):
    list_display = ('store_name', 'store_id', 'city', 'state', 'pin_code', 'owner')
    search_fields = ('store_name', 'city', 'state', 'owner__username')


@admin.register(ServiceArea)
class ServiceAreaAdmin(admin.ModelAdmin):
    list_display = ('store', 'city', 'state', 'estimated_days', 'delivery_fee', 'is_active')
    list_filter = ('state', 'is_active')


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('sku', 'product_name', 'store', 'category', 'price', 'available_stock_count', 'availability')
    list_filter = ('category', 'availability', 'store')
    search_fields = ('sku', 'product_name', 'company_name')


admin.site.register(Cart)
admin.site.register(CartItem)
admin.site.register(Order)
admin.site.register(OrderItem)
admin.site.register(OrderStatusLog)
admin.site.register(Shipment)
admin.site.register(ProductReview)
admin.site.register(ProductAudit)
admin.site.register(Paymentdetails)
admin.site.register(Notification)
