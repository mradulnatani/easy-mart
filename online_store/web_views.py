import csv
from decimal import Decimal
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model
from django.db.models import F, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken

from .auth import (
    clear_jwt_cookies,
    jwt_login_required,
    retailer_required,
    set_jwt_cookies,
    wholesaler_required,
)
from .models import (
    Cart,
    CartItem,
    Notification,
    Order,
    Paymentdetails,
    ProductAudit,
    ServiceArea,
    Shipment,
    Stock,
    StoreDetails,
)
from .qr import generate_qr_png, generate_qr_svg
from .recommendations import get_retailer_recommendations
from .services import (
    BusinessRuleError,
    adjust_stock_quantity,
    apply_credit_to_order,
    confirm_order_delivery,
    create_orders_from_cart,
    ensure_default_store_for_wholesaler,
    import_stock_spreadsheet,
    notify_previous_customers_new_product,
    record_payment,
    update_order_status,
    verify_payment,
)

User = get_user_model()


def _safe_slug(name: str) -> str:
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', (name or 'shop').lower()).strip('-')
    return slug or 'shop'


def home_view(request):
    if request.user.is_authenticated:
        if request.user.is_wholesaler:
            return redirect('wholesaler_dashboard')
        return redirect('retailer_dashboard')

    stores = StoreDetails.objects.prefetch_related('service_areas', 'stocks').order_by('-created_at')
    total_products = Stock.objects.filter(available_stock_count__gt=0).count()
    total_cities = (
        ServiceArea.objects.filter(is_active=True)
        .values('city', 'state')
        .distinct()
        .count()
    )
    return render(
        request,
        'home.html',
        {
            'stores': stores,
            'total_stores': stores.count(),
            'total_products': total_products,
            'total_cities': total_cities,
        },
    )


def login_view(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if request.user.is_authenticated and request.method == 'GET':
        return redirect(next_url or 'home')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(username=username, password=password)
        if user is not None:
            target = next_url or ('wholesaler_dashboard' if user.is_wholesaler else 'retailer_dashboard')
            response = redirect(target)
            set_jwt_cookies(response, user)
            messages.success(request, f'Welcome back, {user.display_name}.')
            return response
        messages.error(request, 'Invalid username or password.')

    return render(request, 'auth/login.html', {'next': next_url})


def register_view(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')
        role = request.POST.get('role', User.Roles.RETAILER)
        business_name = request.POST.get('business_name', '').strip()
        gst_number = request.POST.get('gst_number', '').strip()
        phone_number = request.POST.get('phone_number', '').strip()
        shipping_address = request.POST.get('shipping_address', '').strip()
        city = request.POST.get('city', '').strip()
        state = request.POST.get('state', '').strip()
        pin_code = request.POST.get('pin_code', '').strip()

        if role not in User.Roles.values:
            role = User.Roles.RETAILER

        error = None
        if not username or not password or not phone_number or not shipping_address:
            error = 'Username, password, phone number, and address are required.'
        elif password != confirm_password:
            error = 'Passwords do not match.'
        elif User.objects.filter(username__iexact=username).exists():
            error = 'Username is already taken.'
        elif email and User.objects.filter(email__iexact=email).exists():
            error = 'Email is already registered.'

        if error:
            messages.error(request, error)
            return render(request, 'auth/register.html', {'next': next_url, 'form_data': request.POST})

        user = User(
            username=username,
            email=email,
            role=role,
            business_name=business_name,
            gst_number=gst_number,
            phone_number=phone_number,
            shipping_address=shipping_address,
            city=city,
            state=state,
            pin_code=pin_code,
        )
        user.set_password(password)
        user.save()
        if user.is_wholesaler:
            ensure_default_store_for_wholesaler(user)

        target = next_url or ('wholesaler_dashboard' if user.is_wholesaler else 'retailer_dashboard')
        response = redirect(target)
        set_jwt_cookies(response, user)
        messages.success(request, 'Account created and authenticated via JWT.')
        return response

    initial_role = request.GET.get('role', '').strip().upper()
    form_data = {'role': initial_role} if initial_role in User.Roles.values else {}
    return render(request, 'auth/register.html', {'next': next_url, 'form_data': form_data})


def logout_view(request):
    raw_refresh = request.COOKIES.get(settings.JWT_COOKIE_REFRESH)
    if raw_refresh:
        try:
            RefreshToken(raw_refresh).blacklist()
        except Exception:
            pass
    response = redirect('login_page')
    clear_jwt_cookies(response)
    messages.info(request, 'You have been logged out.')
    return response


@jwt_login_required
def profile_view(request):
    if request.method == 'POST':
        user = request.user
        user.business_name = request.POST.get('business_name', '').strip()
        user.gst_number = request.POST.get('gst_number', '').strip()
        user.phone_number = request.POST.get('phone_number', user.phone_number).strip()
        user.shipping_address = request.POST.get('shipping_address', user.shipping_address).strip()
        user.city = request.POST.get('city', '').strip()
        user.state = request.POST.get('state', '').strip()
        user.pin_code = request.POST.get('pin_code', '').strip()
        user.email = request.POST.get('email', user.email).strip()
        user.save()
        messages.success(request, 'Profile updated.')
        return redirect('profile_page')
    return render(request, 'auth/profile.html')


# ---------------------------------------------------------------------------
# Public / QR-Scanned Wholesaler Storefront
# ---------------------------------------------------------------------------

def shop_products_view(request, store_id):
    """Target URL encoded in the wholesaler's shop QR code."""
    store = get_object_or_404(
        StoreDetails.objects.select_related('owner').prefetch_related('service_areas'),
        store_id=store_id,
    )
    products = Stock.objects.filter(store=store)
    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    in_stock_only = request.GET.get('in_stock') == '1'

    if q:
        products = products.filter(
            Q(product_name__icontains=q) | Q(company_name__icontains=q) | Q(sku__icontains=q)
        )
    if category:
        products = products.filter(category__iexact=category)
    if in_stock_only:
        products = products.filter(available_stock_count__gt=0)

    categories = (
        Stock.objects.filter(store=store)
        .values_list('category', flat=True)
        .distinct()
        .order_by('category')
    )
    service_areas = store.service_areas.filter(is_active=True)

    return render(
        request,
        'shop/store_products.html',
        {
            'store': store,
            'products': products,
            'categories': categories,
            'service_areas': service_areas,
            'q': q,
            'selected_category': category,
            'in_stock_only': in_stock_only,
        },
    )


# ---------------------------------------------------------------------------
# Wholesaler Portal
# ---------------------------------------------------------------------------

@wholesaler_required
def wholesaler_dashboard(request):
    ensure_default_store_for_wholesaler(request.user)
    stores = StoreDetails.objects.filter(owner=request.user).prefetch_related('service_areas')
    stocks = Stock.objects.filter(store__owner=request.user)
    orders = Order.objects.filter(store__owner=request.user).select_related('customer', 'store', 'shipment')
    payments = Paymentdetails.objects.filter(order__store__owner=request.user)

    low_stock_items = stocks.filter(available_stock_count__lte=F('low_stock_threshold')).order_by('available_stock_count')[:8]
    pending_orders = orders.filter(status__in=[Order.OrderStatus.PENDING, Order.OrderStatus.CONFIRMED, Order.OrderStatus.PACKED])
    in_transit_orders = orders.filter(status__in=[Order.OrderStatus.SHIPPED, Order.OrderStatus.IN_TRANSIT, Order.OrderStatus.OUT_FOR_DELIVERY])

    active_orders = orders.exclude(status=Order.OrderStatus.CANCELLED)
    total_collected = sum((o.amount_paid for o in active_orders), Decimal('0.00'))
    total_receivable = sum((o.balance_due for o in active_orders), Decimal('0.00'))

    pending_verifications = payments.filter(status=Paymentdetails.VerificationStatus.PENDING).count()

    return render(
        request,
        'wholesaler/dashboard.html',
        {
            'stores': stores,
            'total_products': stocks.count(),
            'low_stock_count': stocks.filter(available_stock_count__lte=F('low_stock_threshold')).count(),
            'low_stock_items': low_stock_items,
            'pending_orders_count': pending_orders.count(),
            'in_transit_count': in_transit_orders.count(),
            'total_collected': total_collected,
            'total_receivable': total_receivable,
            'pending_verifications': pending_verifications,
            'recent_orders': orders[:8],
        },
    )


@wholesaler_required
def wholesaler_stores_view(request):
    ensure_default_store_for_wholesaler(request.user)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'create_store':
            store_name = request.POST.get('store_name', '').strip()
            store_address = request.POST.get('store_address', '').strip()
            city = request.POST.get('city', '').strip()
            state = request.POST.get('state', '').strip()
            pin_code = request.POST.get('pin_code', '').strip()
            phone_number = request.POST.get('phone_number', '').strip()

            if not store_name or not store_address or not pin_code:
                messages.error(request, 'Store name, address, and PIN code are required.')
            else:
                StoreDetails.objects.create(
                    store_name=store_name,
                    store_address=store_address,
                    city=city,
                    state=state,
                    pin_code=pin_code,
                    phone_number=phone_number,
                    owner=request.user,
                )
                messages.success(request, f'Store "{store_name}" registered.')
            return redirect('wholesaler_stores')

        if action == 'add_service_area':
            store = get_object_or_404(StoreDetails, store_id=request.POST.get('store_id'), owner=request.user)
            state = request.POST.get('state', '').strip()
            city = request.POST.get('city', '').strip()
            pin_code = request.POST.get('pin_code', '').strip()
            estimated_days = int(request.POST.get('estimated_days') or 2)
            delivery_fee = Decimal(request.POST.get('delivery_fee') or '0.00')
            if not state or not city:
                messages.error(request, 'State and City are required for a service area.')
            else:
                ServiceArea.objects.update_or_create(
                    store=store,
                    state=state,
                    city=city,
                    defaults={
                        'pin_code': pin_code,
                        'estimated_days': estimated_days,
                        'delivery_fee': delivery_fee,
                        'is_active': True,
                    },
                )
                messages.success(request, f'Added delivery coverage for {city}, {state}.')
            return redirect('wholesaler_stores')

        if action == 'toggle_service_area':
            area = get_object_or_404(ServiceArea, pk=request.POST.get('area_id'), store__owner=request.user)
            area.is_active = not area.is_active
            area.save(update_fields=['is_active'])
            return redirect('wholesaler_stores')

    stores = StoreDetails.objects.filter(owner=request.user).prefetch_related('service_areas')
    store_cards = []
    for s in stores:
        shop_url = request.build_absolute_uri(reverse('shop_products', kwargs={'store_id': s.store_id}))
        store_cards.append({'store': s, 'shop_url': shop_url})

    return render(request, 'wholesaler/stores.html', {'store_cards': store_cards})


@wholesaler_required
def wholesaler_store_qr_view(request, store_id):
    store = get_object_or_404(StoreDetails, store_id=store_id, owner=request.user)
    shop_url = request.build_absolute_uri(reverse('shop_products', kwargs={'store_id': store.store_id}))
    qr_svg = generate_qr_svg(shop_url, box_size=7, border=3)
    return render(
        request,
        'wholesaler/store_qr.html',
        {
            'store': store,
            'shop_url': shop_url,
            'qr_svg': qr_svg,
        },
    )


@wholesaler_required
def wholesaler_store_qr_download(request, store_id):
    store = get_object_or_404(StoreDetails, store_id=store_id, owner=request.user)
    shop_url = request.build_absolute_uri(reverse('shop_products', kwargs={'store_id': store.store_id}))
    fmt = request.GET.get('format', 'png').lower()
    slug = _safe_slug(store.store_name)

    if fmt == 'svg':
        svg_data = generate_qr_svg(shop_url, box_size=10, border=4)
        response = HttpResponse(svg_data, content_type='image/svg+xml')
        response['Content-Disposition'] = f'attachment; filename="{slug}-shop-qr.svg"'
        return response

    png_bytes = generate_qr_png(shop_url, box_size=12, border=4)
    response = HttpResponse(png_bytes, content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="{slug}-shop-qr.png"'
    return response


def _resolve_wholesaler_store(request):
    store_id = (request.POST.get('store_id') or '').strip()
    if store_id:
        try:
            store = StoreDetails.objects.filter(store_id=store_id, owner=request.user).first()
            if store:
                return store
        except Exception:
            pass
    return StoreDetails.objects.filter(owner=request.user).first() or ensure_default_store_for_wholesaler(request.user)


@wholesaler_required
def wholesaler_inventory_view(request):
    ensure_default_store_for_wholesaler(request.user)
    stores = StoreDetails.objects.filter(owner=request.user)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add_product':
            store = _resolve_wholesaler_store(request)
            if not store:
                messages.error(request, 'Please register a wholesale store first.')
                return redirect('wholesaler_stores')
            try:
                qty = int(request.POST.get('available_stock_count') or 0)
                price = Decimal(request.POST.get('price') or '0')
                gst_rate = Decimal(request.POST.get('gst_rate') or '0')
                moq = int(request.POST.get('min_order_quantity') or 1)
                threshold = int(request.POST.get('low_stock_threshold') or 10)
            except Exception:
                messages.error(request, 'Invalid numeric values provided.')
                return redirect('wholesaler_inventory')

            product = Stock.objects.create(
                store=store,
                sku=request.POST.get('sku', '').strip(),
                product_name=request.POST.get('product_name', '').strip(),
                category=request.POST.get('category', 'General').strip(),
                company_name=request.POST.get('company_name', '').strip(),
                capacity=request.POST.get('capacity', '').strip(),
                unit_type=request.POST.get('unit_type', Stock.UnitTypes.BOX),
                min_order_quantity=moq,
                price=price,
                gst_rate=gst_rate,
                available_stock_count=qty,
                low_stock_threshold=threshold,
            )
            ProductAudit.objects.create(
                product=product,
                changed_by=request.user,
                old_quantity=0,
                new_quantity=qty,
                reason=ProductAudit.ReasonClass.STOCK_RESTOCK,
                notes='Initial product entry',
            )
            notify_previous_customers_new_product(store=store, products=[product])
            messages.success(request, f'Added "{product.product_name}".')
            return redirect('wholesaler_inventory')

        if action == 'adjust_stock':
            product = get_object_or_404(Stock, pk=request.POST.get('product_id'), store__owner=request.user)
            try:
                new_qty = int(request.POST.get('new_quantity'))
                reason = request.POST.get('reason', ProductAudit.ReasonClass.STOCK_RESTOCK)
                notes = request.POST.get('notes', '').strip()
                adjust_stock_quantity(
                    stock=product,
                    new_quantity=new_qty,
                    user=request.user,
                    reason=reason,
                    notes=notes or 'Manual dashboard update',
                )
                messages.success(request, f'Updated stock for {product.product_name} to {new_qty}.')
            except (ValueError, BusinessRuleError) as exc:
                messages.error(request, str(exc))
            return redirect('wholesaler_inventory')

        if action in ('upload_csv', 'upload_spreadsheet'):
            store = _resolve_wholesaler_store(request)
            if not store:
                messages.error(request, 'Please register a wholesale store first.')
                return redirect('wholesaler_stores')
            upload_file = request.FILES.get('file')
            fname = upload_file.name.lower() if upload_file else ''
            if not upload_file or not (fname.endswith('.csv') or fname.endswith('.xlsx')):
                messages.error(request, 'Please select a valid .csv or .xlsx Excel file.')
                return redirect('wholesaler_inventory')
            try:
                res = import_stock_spreadsheet(upload_file=upload_file, store=store, user=request.user)
                messages.success(request, f"Spreadsheet imported: {res['created']} created, {res['updated']} updated.")
            except BusinessRuleError as exc:
                messages.error(request, f'Spreadsheet import failed: {exc}')
            return redirect('wholesaler_inventory')

    stocks = Stock.objects.filter(store__owner=request.user).select_related('store')
    q = request.GET.get('q', '').strip()
    store_filter = request.GET.get('store_id', '').strip()
    low_only = request.GET.get('low_only') == '1'

    if q:
        stocks = stocks.filter(
            Q(product_name__icontains=q) | Q(sku__icontains=q) | Q(company_name__icontains=q)
        )
    if store_filter:
        stocks = stocks.filter(store__store_id=store_filter)
    if low_only:
        stocks = stocks.filter(available_stock_count__lte=F('low_stock_threshold'))

    return render(
        request,
        'wholesaler/inventory.html',
        {
            'stores': stores,
            'stocks': stocks,
            'unit_types': Stock.UnitTypes.choices,
            'audit_reasons': [
                (ProductAudit.ReasonClass.STOCK_RESTOCK, 'Stock Restock'),
                (ProductAudit.ReasonClass.DISCREPANCY_CORRECTION, 'Discrepancy Correction'),
                (ProductAudit.ReasonClass.DAMAGED_GOODS, 'Damaged Goods'),
            ],
            'q': q,
            'store_filter': store_filter,
            'low_only': low_only,
        },
    )


@wholesaler_required
def wholesaler_inventory_export_csv(request):
    stocks = Stock.objects.filter(store__owner=request.user).select_related('store')
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="easymart-inventory.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'sku', 'product_name', 'category', 'company_name', 'capacity',
        'unit_type', 'min_order_quantity', 'price', 'gst_rate',
        'available_stock_count', 'low_stock_threshold', 'store_name',
    ])
    for s in stocks:
        writer.writerow([
            s.sku, s.product_name, s.category, s.company_name, s.capacity,
            s.unit_type, s.min_order_quantity, s.price, s.gst_rate,
            s.available_stock_count, s.low_stock_threshold, s.store.store_name,
        ])
    return response


@wholesaler_required
def wholesaler_audit_log_view(request):
    audits = ProductAudit.objects.filter(product__store__owner=request.user).select_related(
        'product', 'product__store', 'changed_by', 'order'
    )[:200]
    return render(request, 'wholesaler/audit_log.html', {'audits': audits})


@wholesaler_required
def wholesaler_orders_view(request):
    orders = Order.objects.filter(store__owner=request.user).select_related('customer', 'store', 'shipment')
    status_filter = request.GET.get('status', '').strip()
    city_filter = request.GET.get('city', '').strip()
    payment_filter = request.GET.get('payment_status', '').strip()

    if status_filter:
        orders = orders.filter(status=status_filter)
    if city_filter:
        orders = orders.filter(delivery_city__icontains=city_filter)
    if payment_filter:
        orders = orders.filter(payment_status=payment_filter)

    return render(
        request,
        'wholesaler/orders.html',
        {
            'orders': orders,
            'status_choices': Order.OrderStatus.choices,
            'payment_choices': Order.PaymentStatus.choices,
            'status_filter': status_filter,
            'city_filter': city_filter,
            'payment_filter': payment_filter,
        },
    )


@wholesaler_required
def wholesaler_order_detail_view(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related('store', 'customer', 'shipment').prefetch_related(
            'items__product', 'status_logs__changed_by', 'payments'
        ),
        order_id=order_id,
        store__owner=request.user,
    )
    shipment, _ = Shipment.objects.get_or_create(order=order)

    if request.method == 'POST':
        action = request.POST.get('action')
        if order.status in (Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED):
            if action in ('record_payment', 'verify_payment') and order.can_manage_payments:
                pass
            else:
                messages.error(request, 'Completed and cancelled orders are read-only.')
                return redirect('wholesaler_order_detail', order_id=order.order_id)
        if action == 'update_status':
            new_status = request.POST.get('status')
            remarks = request.POST.get('remarks', '').strip()
            location_city = request.POST.get('location_city', '').strip()
            try:
                update_order_status(
                    order=order,
                    new_status=new_status,
                    user=request.user,
                    remarks=remarks,
                    location_city=location_city,
                )
                messages.success(request, f'Order updated to {order.get_status_display() if order.status == new_status else new_status}.')
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            return redirect('wholesaler_order_detail', order_id=order.order_id)

        if action == 'update_shipment':
            shipment.tracking_number = request.POST.get('tracking_number', '').strip()
            shipment.transport_company = request.POST.get('transport_company', '').strip()
            shipment.driver_name = request.POST.get('driver_name', '').strip()
            shipment.driver_phone = request.POST.get('driver_phone', '').strip()
            shipment.vehicle_number = request.POST.get('vehicle_number', '').strip()
            shipment.current_city = request.POST.get('current_city', '').strip()
            shipment.current_state = request.POST.get('current_state', '').strip()
            est_date = request.POST.get('estimated_delivery_date', '').strip()
            shipment.estimated_delivery_date = est_date or None
            shipment.delivery_notes = request.POST.get('delivery_notes', '').strip()
            shipment.save()
            messages.success(request, 'Delivery & shipment details saved.')
            return redirect('wholesaler_order_detail', order_id=order.order_id)

        if action == 'record_payment':
            try:
                amount = Decimal(request.POST.get('amount') or '0')
                record_payment(
                    order=order,
                    retailer=order.customer,
                    amount=amount,
                    payment_method=request.POST.get('payment_method', Paymentdetails.PaymentMethod.CASH),
                    reference_number=request.POST.get('reference_number', '').strip(),
                    notes=request.POST.get('notes', '').strip(),
                    recorded_by=request.user,
                    auto_verify=True,
                )
                messages.success(request, f'Recorded verified payment of {amount}.')
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect('wholesaler_order_detail', order_id=order.order_id)

        if action == 'verify_payment':
            payment = get_object_or_404(Paymentdetails, payment_id=request.POST.get('payment_id'), order=order)
            approved = request.POST.get('decision') == 'approve'
            try:
                verify_payment(payment=payment, wholesaler=request.user, approved=approved)
                messages.success(request, 'Payment verification updated.')
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            return redirect('wholesaler_order_detail', order_id=order.order_id)

    wholesaler_status_choices = [
        (val, label) for val, label in Order.OrderStatus.choices
        if val != Order.OrderStatus.COMPLETED
    ]
    return render(
        request,
        'wholesaler/order_detail.html',
        {
            'order': order,
            'shipment': shipment,
            'status_choices': wholesaler_status_choices,
            'payment_methods': [
                (val, label) for val, label in Paymentdetails.PaymentMethod.choices
                if val != Paymentdetails.PaymentMethod.CREDIT
            ],
        },
    )


@wholesaler_required
def wholesaler_payments_view(request):
    if request.method == 'POST':
        payment = get_object_or_404(
            Paymentdetails,
            payment_id=request.POST.get('payment_id'),
            order__store__owner=request.user,
        )
        approved = request.POST.get('decision') == 'approve'
        try:
            verify_payment(payment=payment, wholesaler=request.user, approved=approved)
            messages.success(request, 'Payment status updated.')
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect('wholesaler_payments')

    payments = Paymentdetails.objects.filter(order__store__owner=request.user).select_related(
        'order', 'retailer', 'order__store'
    )
    active_orders = Order.objects.filter(store__owner=request.user).exclude(status=Order.OrderStatus.CANCELLED).select_related('customer')

    ledger_map = {}
    for order in active_orders:
        cid = order.customer_id
        if cid not in ledger_map:
            ledger_map[cid] = {
                'retailer': order.customer,
                'orders_count': 0,
                'total_billed': Decimal('0.00'),
                'total_paid': Decimal('0.00'),
                'balance_due': Decimal('0.00'),
            }
        entry = ledger_map[cid]
        entry['orders_count'] += 1
        entry['total_billed'] += order.grand_total
        entry['total_paid'] += order.amount_paid
        entry['balance_due'] += order.balance_due

    return render(
        request,
        'wholesaler/payments.html',
        {
            'payments': payments,
            'retailer_ledger': list(ledger_map.values()),
        },
    )


# ---------------------------------------------------------------------------
# Retailer Portal
# ---------------------------------------------------------------------------

@retailer_required
def retailer_dashboard(request):
    orders = Order.objects.filter(customer=request.user).select_related('store', 'shipment')
    active_orders = orders.exclude(status=Order.OrderStatus.CANCELLED)
    total_due = sum((o.balance_due for o in active_orders), Decimal('0.00'))
    in_transit = orders.filter(
        status__in=[
            Order.OrderStatus.SHIPPED,
            Order.OrderStatus.IN_TRANSIT,
            Order.OrderStatus.OUT_FOR_DELIVERY,
        ]
    ).count()

    for w in User.objects.filter(role=User.Roles.WHOLESALER, stores__isnull=True):
        ensure_default_store_for_wholesaler(w)

    stores = StoreDetails.objects.prefetch_related('service_areas', 'stocks').order_by('-created_at')
    unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False)[:5]
    recommendations = get_retailer_recommendations(request.user, limit=6)
    return render(
        request,
        'retailer/dashboard.html',
        {
            'recent_orders': orders[:6],
            'total_orders': orders.count(),
            'in_transit_count': in_transit,
            'total_due': total_due,
            'stores': stores,
            'unread_notifications': unread_notifications,
            'recommendations': recommendations,
        },
    )


@retailer_required
def retailer_shops_view(request):
    for w in User.objects.filter(role=User.Roles.WHOLESALER, stores__isnull=True):
        ensure_default_store_for_wholesaler(w)

    stores = StoreDetails.objects.select_related('owner').prefetch_related('service_areas', 'stocks').order_by('-created_at')
    q = request.GET.get('q', '').strip()
    city = request.GET.get('city', '').strip()
    state = request.GET.get('state', '').strip()

    if q:
        stores = stores.filter(
            Q(store_name__icontains=q) | Q(owner__business_name__icontains=q) | Q(owner__username__icontains=q)
        )
    if city:
        stores = stores.filter(
            Q(city__icontains=city) | Q(service_areas__city__icontains=city, service_areas__is_active=True)
        ).distinct()
    if state:
        stores = stores.filter(
            Q(state__icontains=state) | Q(service_areas__state__icontains=state, service_areas__is_active=True)
        ).distinct()

    return render(
        request,
        'retailer/shops.html',
        {
            'stores': stores,
            'q': q,
            'city': city,
            'state': state,
        },
    )


@retailer_required
def retailer_catalog_view(request):
    products = Stock.objects.select_related('store').prefetch_related('store__service_areas')
    q = request.GET.get('q', '').strip()
    city = request.GET.get('city', '').strip()
    state = request.GET.get('state', '').strip()
    store_id = request.GET.get('store_id', '').strip()
    category = request.GET.get('category', '').strip()

    if q:
        products = products.filter(
            Q(product_name__icontains=q) | Q(company_name__icontains=q) | Q(sku__icontains=q)
        )
    if city:
        products = products.filter(
            Q(store__city__icontains=city)
            | Q(store__service_areas__city__icontains=city, store__service_areas__is_active=True)
        ).distinct()
    if state:
        products = products.filter(
            Q(store__state__icontains=state)
            | Q(store__service_areas__state__icontains=state, store__service_areas__is_active=True)
        ).distinct()
    if store_id:
        products = products.filter(store__store_id=store_id)
    if category:
        products = products.filter(category__iexact=category)

    stores = StoreDetails.objects.all().order_by('store_name')
    categories = Stock.objects.values_list('category', flat=True).distinct().order_by('category')
    recommendations = []
    if not any([q, city, state, store_id, category]):
        recommendations = get_retailer_recommendations(request.user, limit=4)

    return render(
        request,
        'retailer/catalog.html',
        {
            'products': products,
            'stores': stores,
            'categories': categories,
            'recommendations': recommendations,
            'q': q,
            'city': city,
            'state': state,
            'store_id': store_id,
            'category': category,
        },
    )


@retailer_required
def add_to_cart_view(request, product_id):
    if request.method != 'POST':
        return redirect('retailer_catalog')

    product = get_object_or_404(Stock, pk=product_id)
    try:
        qty = int(request.POST.get('quantity') or product.min_order_quantity)
    except ValueError:
        qty = product.min_order_quantity

    if qty < product.min_order_quantity:
        messages.error(request, f'Minimum order quantity for {product.product_name} is {product.min_order_quantity}.')
        return redirect(request.POST.get('next') or 'retailer_catalog')

    if qty > product.available_stock_count:
        messages.error(request, f'Only {product.available_stock_count} units available for {product.product_name}.')
        return redirect(request.POST.get('next') or 'retailer_catalog')

    cart, _ = Cart.objects.get_or_create(retailer=request.user)
    item, created = CartItem.objects.get_or_create(cart=cart, product=product, defaults={'quantity': qty})
    if not created:
        new_qty = item.quantity + qty
        if new_qty > product.available_stock_count:
            messages.error(request, f'Cannot exceed available stock ({product.available_stock_count}).')
            return redirect(request.POST.get('next') or 'retailer_cart')
        item.quantity = new_qty
        item.save(update_fields=['quantity'])

    messages.success(request, f'Added {qty} x {product.product_name} to cart.')
    return redirect(request.POST.get('next') or 'retailer_cart')


@retailer_required
def retailer_cart_view(request):
    cart, _ = Cart.objects.get_or_create(retailer=request.user)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_item':
            item = get_object_or_404(CartItem, pk=request.POST.get('item_id'), cart=cart)
            try:
                qty = int(request.POST.get('quantity') or 0)
            except ValueError:
                qty = 0
            if qty <= 0:
                item.delete()
                messages.info(request, f'Removed {item.product.product_name} from cart.')
            elif qty < item.product.min_order_quantity:
                messages.error(request, f'Minimum order quantity is {item.product.min_order_quantity}.')
            elif qty > item.product.available_stock_count:
                messages.error(request, f'Only {item.product.available_stock_count} units in stock.')
            else:
                item.quantity = qty
                item.save(update_fields=['quantity'])
                messages.success(request, 'Cart updated.')
            return redirect('retailer_cart')

        if action == 'remove_item':
            item = get_object_or_404(CartItem, pk=request.POST.get('item_id'), cart=cart)
            item.delete()
            return redirect('retailer_cart')

        if action == 'checkout':
            use_credit = request.POST.get('payment_choice') == 'credit'
            try:
                orders = create_orders_from_cart(
                    retailer=request.user,
                    delivery_address=request.POST.get('delivery_address', request.user.shipping_address).strip(),
                    delivery_city=request.POST.get('delivery_city', request.user.city).strip(),
                    delivery_state=request.POST.get('delivery_state', request.user.state).strip(),
                    delivery_pincode=request.POST.get('delivery_pincode', request.user.pin_code).strip(),
                    contact_phone=request.POST.get('contact_phone', request.user.phone_number).strip(),
                    notes=request.POST.get('notes', '').strip(),
                    use_credit=use_credit,
                )
                msg = (
                    f'Placed {len(orders)} order(s) on credit (₹10,000 limit)!'
                    if use_credit
                    else f'Placed {len(orders)} order(s) successfully!'
                )
                messages.success(request, msg)
                if len(orders) == 1:
                    return redirect('retailer_order_detail', order_id=orders[0].order_id)
                return redirect('retailer_orders')
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
                return redirect('retailer_cart')

    items = cart.items.select_related('product', 'product__store')
    return render(request, 'retailer/cart.html', {'cart': cart, 'items': items})


@retailer_required
def retailer_orders_view(request):
    orders = Order.objects.filter(customer=request.user).select_related('store', 'shipment')
    status_filter = request.GET.get('status', '').strip()
    if status_filter:
        orders = orders.filter(status=status_filter)
    return render(
        request,
        'retailer/orders.html',
        {
            'orders': orders,
            'status_choices': Order.OrderStatus.choices,
            'status_filter': status_filter,
        },
    )


@retailer_required
def retailer_order_detail_view(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related('store', 'customer', 'shipment').prefetch_related(
            'items__product', 'status_logs__changed_by', 'payments'
        ),
        order_id=order_id,
        customer=request.user,
    )

    if request.method == 'POST':
        action = request.POST.get('action')
        if order.status in (Order.OrderStatus.COMPLETED, Order.OrderStatus.CANCELLED):
            if action == 'reorder':
                pass
            elif action == 'submit_payment' and order.can_manage_payments:
                pass
            else:
                messages.error(request, 'Completed and cancelled orders are read-only.')
                return redirect('retailer_order_detail', order_id=order.order_id)
        if action == 'cancel_order':
            if order.status != Order.OrderStatus.PENDING:
                messages.error(request, 'Only pending orders can be cancelled.')
            else:
                try:
                    update_order_status(
                        order=order,
                        new_status=Order.OrderStatus.CANCELLED,
                        user=request.user,
                        remarks='Cancelled by retailer',
                    )
                    messages.info(request, 'Order cancelled and stock restored.')
                except BusinessRuleError as exc:
                    messages.error(request, str(exc))
            return redirect('retailer_order_detail', order_id=order.order_id)

        if action == 'reorder':
            cart, _ = Cart.objects.get_or_create(retailer=request.user)
            added = 0
            for item in order.items.select_related('product'):
                if item.product.available_stock_count >= item.quantity:
                    ci, created = CartItem.objects.get_or_create(
                        cart=cart, product=item.product, defaults={'quantity': item.quantity}
                    )
                    if not created:
                        ci.quantity = min(ci.quantity + item.quantity, item.product.available_stock_count)
                        ci.save(update_fields=['quantity'])
                    added += 1
            messages.success(request, f'Added {added} item(s) from order {order.short_id} to your cart.')
            return redirect('retailer_cart')

        if action == 'confirm_delivery':
            try:
                confirm_order_delivery(
                    order=order,
                    retailer=request.user,
                    remarks=request.POST.get('remarks', '').strip(),
                )
                messages.success(request, f'Delivery confirmed! Order #{order.short_id} is now Completed.')
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            return redirect('retailer_order_detail', order_id=order.order_id)

        if action == 'use_credit':
            try:
                apply_credit_to_order(order=order, retailer=request.user)
                messages.success(request, f'Order #{order.short_id} deferred to your ₹10,000 credit limit.')
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            return redirect('retailer_order_detail', order_id=order.order_id)

        if action == 'submit_payment':
            payment_method = request.POST.get('payment_method', Paymentdetails.PaymentMethod.UPI)
            if payment_method == Paymentdetails.PaymentMethod.CREDIT:
                try:
                    apply_credit_to_order(order=order, retailer=request.user)
                    messages.success(request, f'Order #{order.short_id} deferred to your ₹10,000 credit limit.')
                except BusinessRuleError as exc:
                    messages.error(request, str(exc))
                return redirect('retailer_order_detail', order_id=order.order_id)

            try:
                amount = Decimal(request.POST.get('amount') or '0')
                proof_file = (
                    request.FILES.get('proof_file')
                    if payment_method == Paymentdetails.PaymentMethod.UPI
                    else None
                )
                record_payment(
                    order=order,
                    retailer=request.user,
                    amount=amount,
                    payment_method=payment_method,
                    reference_number=request.POST.get('reference_number', '').strip(),
                    proof_file=proof_file,
                    notes=request.POST.get('notes', '').strip(),
                    recorded_by=request.user,
                    auto_verify=False,
                )
                messages.success(request, 'Payment submitted for wholesaler verification.')
            except Exception as exc:
                messages.error(request, str(exc))
            return redirect('retailer_order_detail', order_id=order.order_id)

    return render(
        request,
        'retailer/order_detail.html',
        {
            'order': order,
            'payment_methods': [
                (val, label) for val, label in Paymentdetails.PaymentMethod.choices
                if val != Paymentdetails.PaymentMethod.CREDIT
            ],
        },
    )


@retailer_required
def retailer_payments_view(request):
    payments = Paymentdetails.objects.filter(retailer=request.user).select_related('order', 'order__store')
    orders = Order.objects.filter(customer=request.user).exclude(status=Order.OrderStatus.CANCELLED).select_related('store')
    total_billed = sum((o.grand_total for o in orders), Decimal('0.00'))
    total_paid = sum((o.amount_paid for o in orders), Decimal('0.00'))
    total_due = sum((o.balance_due for o in orders), Decimal('0.00'))
    return render(
        request,
        'retailer/payments.html',
        {
            'payments': payments,
            'orders': orders,
            'total_billed': total_billed,
            'total_paid': total_paid,
            'total_due': total_due,
        },
    )


@jwt_login_required
def notifications_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'mark_all_read':
            Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
            messages.success(request, 'All notifications marked as read.')
        elif action == 'mark_read':
            notif = get_object_or_404(Notification, pk=request.POST.get('notification_id'), recipient=request.user)
            notif.is_read = True
            notif.save(update_fields=['is_read'])
        return redirect('notifications_page')

    notifications = Notification.objects.filter(recipient=request.user).select_related('store', 'product')
    return render(request, 'notifications/list.html', {'notifications': notifications})


@jwt_login_required
def order_invoice_view(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related('store', 'store__owner', 'customer', 'shipment').prefetch_related('items__product'),
        order_id=order_id,
    )
    if order.customer != request.user and order.store.owner != request.user:
        messages.error(request, 'You are not authorized to view this invoice.')
        return redirect('home')
    return render(request, 'orders/invoice.html', {'order': order})
