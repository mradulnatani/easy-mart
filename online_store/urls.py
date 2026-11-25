from django.urls import path

from . import api_views, web_views

urlpatterns = [
    # HTML / Server-Rendered Frontend Routes
    path('', web_views.home_view, name='home'),
    path('login/', web_views.login_view, name='login_page'),
    path('register/', web_views.register_view, name='register_page'),
    path('logout/', web_views.logout_view, name='logout_page'),
    path('profile/', web_views.profile_view, name='profile_page'),
    path('notifications/', web_views.notifications_view, name='notifications_page'),

    # QR Code Public/Retailer Storefront Destination
    path('shop/<uuid:store_id>/', web_views.shop_products_view, name='shop_products'),

    # Wholesaler Portal
    path('wholesaler/', web_views.wholesaler_dashboard, name='wholesaler_dashboard'),
    path('wholesaler/stores/', web_views.wholesaler_stores_view, name='wholesaler_stores'),
    path('wholesaler/stores/<uuid:store_id>/qr/', web_views.wholesaler_store_qr_view, name='wholesaler_store_qr'),
    path('wholesaler/stores/<uuid:store_id>/qr/download/', web_views.wholesaler_store_qr_download, name='wholesaler_store_qr_download'),
    path('wholesaler/inventory/', web_views.wholesaler_inventory_view, name='wholesaler_inventory'),
    path('wholesaler/inventory/export/', web_views.wholesaler_inventory_export_csv, name='wholesaler_inventory_export'),
    path('wholesaler/inventory/audit/', web_views.wholesaler_audit_log_view, name='wholesaler_audit_log'),
    path('wholesaler/orders/', web_views.wholesaler_orders_view, name='wholesaler_orders'),
    path('wholesaler/orders/<uuid:order_id>/', web_views.wholesaler_order_detail_view, name='wholesaler_order_detail'),
    path('wholesaler/payments/', web_views.wholesaler_payments_view, name='wholesaler_payments'),

    # Retailer Portal
    path('retailer/', web_views.retailer_dashboard, name='retailer_dashboard'),
    path('retailer/shops/', web_views.retailer_shops_view, name='retailer_shops'),
    path('retailer/catalog/', web_views.retailer_catalog_view, name='retailer_catalog'),
    path('retailer/cart/', web_views.retailer_cart_view, name='retailer_cart'),
    path('retailer/cart/add/<int:product_id>/', web_views.add_to_cart_view, name='add_to_cart'),
    path('retailer/orders/', web_views.retailer_orders_view, name='retailer_orders'),
    path('retailer/orders/<uuid:order_id>/', web_views.retailer_order_detail_view, name='retailer_order_detail'),
    path('retailer/payments/', web_views.retailer_payments_view, name='retailer_payments'),

    # Shared Invoice View
    path('orders/<uuid:order_id>/invoice/', web_views.order_invoice_view, name='order_invoice'),

    # REST API Endpoints (JWT Bearer or Cookie)
    path('api/products/', api_views.availableproducts, name='available_stock'),
    path('api/uploadviacsv/', api_views.EnterStockViaCSVView.as_view(), name='enter_stock_details_via_csv'),
    path('api/editproduct/<int:pk>/', api_views.editstock, name='edit_stock'),
    path('api/register/', api_views.RegisterAPIView.as_view(), name='register'),
    path('api/login/', api_views.LoginAPIView.as_view(), name='login'),
    path('api/profile/', api_views.ProfileAPIView.as_view(), name='profile'),
    path('api/logout/', api_views.LogoutAPIView.as_view(), name='logout'),
    path('api/stores/', api_views.StoreRegistrationView.as_view(), name='register_store'),
    path('api/stores/<uuid:store_id>/service-areas/', api_views.ServiceAreaListCreateAPIView.as_view(), name='service_areas_api'),
    path('api/stores/<uuid:store_id>/qr/download/', api_views.StoreQRDownloadAPIView.as_view(), name='store_qr_api'),
    path('api/orders/', api_views.OrderListCreateAPIView.as_view(), name='orders_api'),
    path('api/orders/<uuid:order_id>/', api_views.OrderDetailAPIView.as_view(), name='order_detail_api'),
    path('api/orders/<uuid:order_id>/status/', api_views.OrderStatusUpdateAPIView.as_view(), name='order_status_api'),
    path('api/orders/<uuid:order_id>/confirm-delivery/', api_views.OrderConfirmDeliveryAPIView.as_view(), name='order_confirm_delivery_api'),
    path('api/orders/<uuid:order_id>/use-credit/', api_views.OrderApplyCreditAPIView.as_view(), name='order_use_credit_api'),
    path('api/orders/<uuid:order_id>/shipment/', api_views.ShipmentUpdateAPIView.as_view(), name='order_shipment_api'),
    path('api/payments/', api_views.PaymentListCreateAPIView.as_view(), name='payments_api'),
    path('api/payments/<uuid:payment_id>/verify/', api_views.PaymentVerifyAPIView.as_view(), name='payment_verify_api'),
    path('api/inventory/audit/', api_views.ProductAuditListAPIView.as_view(), name='audit_api'),
    path('api/notifications/', api_views.NotificationListAPIView.as_view(), name='notifications_api'),
    path('api/recommendations/', api_views.RetailerRecommendationsAPIView.as_view(), name='recommendations_api'),
]
