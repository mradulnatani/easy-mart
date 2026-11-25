from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .auth import clear_jwt_cookies, set_jwt_cookies
from .models import Notification, Order, Paymentdetails, ProductAudit, ServiceArea, Shipment, Stock, StoreDetails
from .permissions import IsRetailer, IsWholesaler
from .qr import generate_qr_png, generate_qr_svg
from .recommendations import get_retailer_recommendations
from .serializer import (
    CSVUploadSerializer,
    NotificationSerializer,
    OrderCreateSerializer,
    OrderSerializer,
    PaymentSerializer,
    ProductAuditSerializer,
    ServiceAreaSerializer,
    ShipmentSerializer,
    StockSerializer,
    StoreRegistrationSerializer,
    UserDetailSerializer,
    UserLoginSerializer,
    UserRegistrationSerializer,
)
from .services import (
    BusinessRuleError,
    adjust_stock_quantity,
    apply_credit_to_order,
    confirm_order_delivery,
    create_orders_for_items,
    ensure_default_store_for_wholesaler,
    import_stock_spreadsheet,
    record_payment,
    update_order_status,
    verify_payment,
)

User = get_user_model()


class EnterStockViaCSVView(APIView):
    parser_classes = (MultiPartParser, FormParser)
    permission_classes = [IsWholesaler]
    serializer_class = CSVUploadSerializer

    def post(self, request, *args, **kwargs):
        if 'file' not in request.FILES:
            return Response({'error': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

        upload_file = request.FILES['file']
        fname = upload_file.name.lower()
        if not (fname.endswith('.csv') or fname.endswith('.xlsx')):
            return Response(
                {'error': 'Unsupported file type. Upload a .csv or .xlsx Excel file.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        store_id = request.data.get('store_id')
        if store_id:
            store = get_object_or_404(StoreDetails, store_id=store_id, owner=request.user)
        else:
            store = StoreDetails.objects.filter(owner=request.user).first() or ensure_default_store_for_wholesaler(request.user)
            if not store:
                return Response({'error': 'Register a store before uploading inventory.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = import_stock_spreadsheet(upload_file=upload_file, store=store, user=request.user)
            return Response(
                {
                    'message': f"Imported {result['created']} new and updated {result['updated']} products.",
                    **result,
                },
                status=status.HTTP_201_CREATED,
            )
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([AllowAny])
def availableproducts(request):
    products = Stock.objects.select_related('store').all()
    store_id = request.GET.get('store_id')
    city = request.GET.get('city')
    state = request.GET.get('state')
    category = request.GET.get('category')
    q = request.GET.get('q')

    if store_id:
        products = products.filter(store__store_id=store_id)
    if city:
        products = products.filter(
            Q(store__city__iexact=city) | Q(store__service_areas__city__iexact=city, store__service_areas__is_active=True)
        ).distinct()
    if state:
        products = products.filter(
            Q(store__state__iexact=state) | Q(store__service_areas__state__iexact=state, store__service_areas__is_active=True)
        ).distinct()
    if category:
        products = products.filter(category__iexact=category)
    if q:
        products = products.filter(
            Q(product_name__icontains=q) | Q(company_name__icontains=q) | Q(sku__icontains=q)
        )

    serializer = StockSerializer(products, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET', 'PUT', 'PATCH'])
@permission_classes([IsWholesaler])
def editstock(request, pk):
    product = get_object_or_404(Stock, pk=pk, store__owner=request.user)
    if request.method == 'GET':
        return Response(StockSerializer(product).data, status=status.HTTP_200_OK)

    old_qty = product.available_stock_count
    partial = request.method == 'PATCH'
    serializer = StockSerializer(product, data=request.data, partial=partial)
    if serializer.is_valid():
        updated = serializer.save()
        if old_qty != updated.available_stock_count:
            ProductAudit.objects.create(
                product=updated,
                changed_by=request.user,
                old_quantity=old_qty,
                new_quantity=updated.available_stock_count,
                reason=request.data.get('reason', ProductAudit.ReasonClass.DISCREPANCY_CORRECTION),
                notes=request.data.get('notes', 'API stock edit'),
            )
        return Response(StockSerializer(updated).data, status=status.HTTP_200_OK)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class RegisterAPIView(generics.CreateAPIView):
    serializer_class = UserRegistrationSerializer
    permission_classes = [AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        response = Response(
            {
                'message': 'Registration successful.',
                'user': UserDetailSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )
        set_jwt_cookies(response, user)
        return response


class LoginAPIView(generics.GenericAPIView):
    serializer_class = UserLoginSerializer
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data['username'],
            password=serializer.validated_data['password'],
        )
        if not user:
            return Response({'error': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)

        response = Response(
            {
                'user_id': user.id,
                'username': user.username,
                'role': user.role,
                'is_wholesaler': user.is_wholesaler,
                'is_retailer': user.is_retailer,
                'is_store_manager': user.is_store_manager,
                'is_customer': user.is_customer,
            },
            status=status.HTTP_200_OK,
        )
        refresh = set_jwt_cookies(response, user)
        response.data['refresh'] = str(refresh)
        response.data['access'] = str(refresh.access_token)
        return response


class ProfileAPIView(generics.RetrieveUpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        raw_refresh = request.data.get('refresh') or request.COOKIES.get(settings.JWT_COOKIE_REFRESH)
        if raw_refresh:
            try:
                token = RefreshToken(raw_refresh)
                token.blacklist()
            except Exception:
                pass
        response = Response({'detail': 'Successfully logged out.'}, status=status.HTTP_200_OK)
        clear_jwt_cookies(response)
        return response


class StoreRegistrationView(generics.ListCreateAPIView):
    serializer_class = StoreRegistrationSerializer
    permission_classes = [IsWholesaler]

    def get_queryset(self):
        return StoreDetails.objects.filter(owner=self.request.user).prefetch_related('service_areas')

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


class ServiceAreaListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ServiceAreaSerializer
    permission_classes = [IsWholesaler]

    def get_queryset(self):
        return ServiceArea.objects.filter(
            store__store_id=self.kwargs['store_id'],
            store__owner=self.request.user,
        )

    def perform_create(self, serializer):
        store = get_object_or_404(StoreDetails, store_id=self.kwargs['store_id'], owner=self.request.user)
        serializer.save(store=store)


class StoreQRDownloadAPIView(APIView):
    permission_classes = [IsWholesaler]

    def get(self, request, store_id):
        store = get_object_or_404(StoreDetails, store_id=store_id, owner=request.user)
        shop_url = request.build_absolute_uri(reverse('shop_products', kwargs={'store_id': store.store_id}))
        fmt = request.GET.get('format', 'png').lower()
        if fmt == 'svg':
            svg_data = generate_qr_svg(shop_url)
            resp = HttpResponse(svg_data, content_type='image/svg+xml')
            resp['Content-Disposition'] = f'attachment; filename="shop-{store.store_id}-qr.svg"'
            return resp
        png_bytes = generate_qr_png(shop_url)
        resp = HttpResponse(png_bytes, content_type='image/png')
        resp['Content-Disposition'] = f'attachment; filename="shop-{store.store_id}-qr.png"'
        return resp


class OrderListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.is_wholesaler:
            orders = Order.objects.filter(store__owner=request.user)
        else:
            orders = Order.objects.filter(customer=request.user)
        orders = orders.select_related('store', 'customer', 'shipment').prefetch_related('items__product', 'status_logs')
        return Response(OrderSerializer(orders, many=True).data)

    def post(self, request):
        if not request.user.is_retailer:
            return Response({'error': 'Only retailers can place orders.'}, status=status.HTTP_403_FORBIDDEN)

        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        items_with_qty = []
        for entry in data['items']:
            stock = get_object_or_404(Stock, pk=entry['product_id'])
            items_with_qty.append((stock, entry['quantity']))

        try:
            orders = create_orders_for_items(
                retailer=request.user,
                items_with_qty=items_with_qty,
                delivery_address=data.get('delivery_address') or request.user.shipping_address,
                delivery_city=data.get('delivery_city') or request.user.city,
                delivery_state=data.get('delivery_state') or request.user.state,
                delivery_pincode=data.get('delivery_pincode') or request.user.pin_code,
                contact_phone=data.get('contact_phone') or request.user.phone_number,
                notes=data.get('notes', ''),
                use_credit=data.get('use_credit', False),
            )
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(orders, many=True).data, status=status.HTTP_201_CREATED)


class OrderDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_id):
        order = get_object_or_404(
            Order.objects.select_related('store', 'customer', 'shipment').prefetch_related('items__product', 'status_logs'),
            order_id=order_id,
        )
        if order.customer != request.user and order.store.owner != request.user:
            return Response({'error': 'Not authorized to view this order.'}, status=status.HTTP_403_FORBIDDEN)
        return Response(OrderSerializer(order).data)


class OrderStatusUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id)
        new_status = request.data.get('status')
        remarks = request.data.get('remarks', '')
        location_city = request.data.get('location_city', '')

        if request.user == order.customer:
            if new_status != Order.OrderStatus.CANCELLED or order.status != Order.OrderStatus.PENDING:
                return Response({'error': 'Retailers can only cancel pending orders.'}, status=status.HTTP_403_FORBIDDEN)
        elif request.user != order.store.owner:
            return Response({'error': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            updated = update_order_status(
                order=order,
                new_status=new_status,
                user=request.user,
                remarks=remarks,
                location_city=location_city,
            )
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OrderSerializer(updated).data)


class ShipmentUpdateAPIView(APIView):
    permission_classes = [IsWholesaler]

    def patch(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, store__owner=request.user)
        if order.status in (Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED):
            return Response(
                {'error': 'Completed and cancelled orders are read-only.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        shipment, _ = Shipment.objects.get_or_create(order=order)
        serializer = ShipmentSerializer(shipment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class PaymentListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.is_wholesaler:
            qs = Paymentdetails.objects.filter(order__store__owner=request.user)
        else:
            qs = Paymentdetails.objects.filter(retailer=request.user)
        return Response(PaymentSerializer(qs.select_related('order', 'retailer'), many=True).data)

    def post(self, request):
        serializer = PaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.validated_data['order']

        if request.user != order.customer and request.user != order.store.owner:
            return Response({'error': 'Forbidden.'}, status=status.HTTP_403_FORBIDDEN)
        if order.status in (Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED):
            return Response(
                {'error': 'Completed and cancelled orders are read-only.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            method = serializer.validated_data['payment_method']
            proof = request.FILES.get('proof_file') if method == Paymentdetails.PaymentMethod.UPI else None
            payment = record_payment(
                order=order,
                retailer=order.customer,
                amount=serializer.validated_data['amount'],
                payment_method=method,
                reference_number=serializer.validated_data.get('reference_number', ''),
                proof_file=proof,
                notes=serializer.validated_data.get('notes', ''),
                recorded_by=request.user,
                auto_verify=(request.user == order.store.owner),
            )
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class PaymentVerifyAPIView(APIView):
    permission_classes = [IsWholesaler]

    def post(self, request, payment_id):
        payment = get_object_or_404(Paymentdetails, payment_id=payment_id, order__store__owner=request.user)
        if payment.order.status in (Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED):
            return Response(
                {'error': 'Completed and cancelled orders are read-only.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        approved = str(request.data.get('approved', 'true')).lower() in ('true', '1', 'yes')
        updated = verify_payment(payment=payment, wholesaler=request.user, approved=approved)
        return Response(PaymentSerializer(updated).data)


class ProductAuditListAPIView(generics.ListAPIView):
    serializer_class = ProductAuditSerializer
    permission_classes = [IsWholesaler]

    def get_queryset(self):
        return ProductAudit.objects.filter(product__store__owner=self.request.user).select_related(
            'product', 'changed_by', 'order'
        )


class OrderConfirmDeliveryAPIView(APIView):
    permission_classes = [IsRetailer]

    def post(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, customer=request.user)
        try:
            updated = confirm_order_delivery(
                order=order,
                retailer=request.user,
                remarks=request.data.get('remarks', ''),
            )
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(updated).data)


class OrderApplyCreditAPIView(APIView):
    permission_classes = [IsRetailer]

    def post(self, request, order_id):
        order = get_object_or_404(Order, order_id=order_id, customer=request.user)
        try:
            updated = apply_credit_to_order(order=order, retailer=request.user)
        except BusinessRuleError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(OrderSerializer(updated).data)


class NotificationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Notification.objects.filter(recipient=request.user)
        return Response(NotificationSerializer(qs, many=True).data)

    def post(self, request):
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({'detail': 'All notifications marked as read.'})


class RetailerRecommendationsAPIView(APIView):
    permission_classes = [IsRetailer]

    def get(self, request):
        limit = min(int(request.GET.get('limit', 6) or 6), 20)
        recs = get_retailer_recommendations(request.user, limit=limit)
        payload = [
            {
                'product': StockSerializer(r['product']).data,
                'score': r['score'],
                'reason': r['reason'],
                'suggested_qty': r['suggested_qty'],
            }
            for r in recs
        ]
        return Response(payload)
