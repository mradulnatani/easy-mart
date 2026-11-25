import csv
from datetime import timedelta
from decimal import Decimal
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import (
    Cart,
    Notification,
    Order,
    OrderItem,
    OrderStatusLog,
    Paymentdetails,
    ProductAudit,
    ServiceArea,
    Shipment,
    Stock,
    StoreDetails,
)


class BusinessRuleError(Exception):
    """Raised when stock, order, or payment rules are violated."""


def ensure_default_store_for_wholesaler(user) -> StoreDetails | None:
    """Ensure a wholesaler has at least one StoreDetails record based on their profile."""
    if not user or not getattr(user, 'is_wholesaler', False):
        return None
    existing = StoreDetails.objects.filter(owner=user).first()
    if existing is not None:
        return existing
    store_name = (user.business_name or '').strip() or f"{user.username}'s Wholesale Shop"
    return StoreDetails.objects.create(
        store_name=store_name,
        store_address=(user.shipping_address or 'Main Market').strip(),
        city=(user.city or '').strip(),
        state=(user.state or '').strip(),
        pin_code=(user.pin_code or '000000').strip(),
        phone_number=(user.phone_number or '').strip(),
        owner=user,
    )


def adjust_stock_quantity(
    *,
    stock: Stock,
    new_quantity: int,
    user,
    reason: str = ProductAudit.ReasonClass.STOCK_RESTOCK,
    order: Order | None = None,
    notes: str = '',
):
    if new_quantity < 0:
        raise BusinessRuleError('Stock count cannot be negative.')

    old_quantity = stock.available_stock_count
    if old_quantity == new_quantity:
        return stock

    stock.available_stock_count = new_quantity
    stock.save()

    ProductAudit.objects.create(
        product=stock,
        changed_by=user,
        order=order,
        old_quantity=old_quantity,
        new_quantity=new_quantity,
        reason=reason,
        notes=notes[:255],
    )
    return stock


def _col_ref_to_index(cell_ref: str) -> int:
    match = re.match(r'^([A-Za-z]+)', cell_ref or '')
    if not match:
        return 0
    idx = 0
    for ch in match.group(1).upper():
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


def _parse_xlsx_rows(file_obj) -> list[dict[str, str]]:
    """Parse the first sheet of an .xlsx workbook into dict rows using stdlib only."""
    raw_bytes = file_obj.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise BusinessRuleError('Invalid Excel (.xlsx) file.') from exc

    with zf:
        shared_strings: list[str] = []
        if 'xl/sharedStrings.xml' in zf.namelist():
            ss_root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
            for si in ss_root:
                text_parts = [elem.text or '' for elem in si.iter() if elem.tag.endswith('}t') or elem.tag == 't']
                shared_strings.append(''.join(text_parts))

        sheet_candidates = [
            name for name in sorted(zf.namelist())
            if re.match(r'^xl/worksheets/sheet\d+\.xml$', name)
        ]
        if not sheet_candidates:
            raise BusinessRuleError('No worksheet found in the Excel file.')

        sheet_root = ET.fromstring(zf.read(sheet_candidates[0]))

    rows: list[list[str]] = []
    for row_el in sheet_root.iter():
        if not (row_el.tag.endswith('}row') or row_el.tag == 'row'):
            continue
        row_cells: dict[int, str] = {}
        fallback_col = 0
        for cell_el in row_el:
            if not (cell_el.tag.endswith('}c') or cell_el.tag == 'c'):
                continue
            ref = cell_el.attrib.get('r')
            col_idx = _col_ref_to_index(ref) if ref else fallback_col
            fallback_col = col_idx + 1
            cell_type = cell_el.attrib.get('t', '')

            val = ''
            if cell_type == 'inlineStr':
                val = ''.join(el.text or '' for el in cell_el.iter() if el.tag.endswith('}t') or el.tag == 't')
            else:
                v_el = next((el for el in cell_el if el.tag.endswith('}v') or el.tag == 'v'), None)
                if v_el is not None and v_el.text is not None:
                    raw_val = v_el.text
                    if cell_type == 's':
                        ss_idx = int(raw_val)
                        val = shared_strings[ss_idx] if 0 <= ss_idx < len(shared_strings) else ''
                    elif cell_type == 'b':
                        val = 'TRUE' if raw_val == '1' else 'FALSE'
                    else:
                        val = raw_val
            row_cells[col_idx] = val

        if row_cells:
            max_col = max(row_cells.keys())
            rows.append([row_cells.get(i, '') for i in range(max_col + 1)])

    if len(rows) < 2:
        return []

    headers = [str(h).strip() for h in rows[0]]
    dict_rows: list[dict[str, str]] = []
    for data_row in rows[1:]:
        if not any(str(c).strip() for c in data_row):
            continue
        row_dict = {
            headers[i]: (str(data_row[i]).strip() if i < len(data_row) else '')
            for i in range(len(headers))
            if headers[i]
        }
        dict_rows.append(row_dict)
    return dict_rows


_HEADER_ALIASES = {
    'product_name': 'product_name',
    'product': 'product_name',
    'item_name': 'product_name',
    'item': 'product_name',
    'name': 'product_name',
    'category': 'category',
    'cat': 'category',
    'price': 'price',
    'unit_price': 'price',
    'rate': 'price',
    'mrp': 'price',
    'gst_rate': 'gst_rate',
    'gst': 'gst_rate',
    'tax': 'gst_rate',
    'tax_rate': 'gst_rate',
    'capacity': 'capacity',
    'pack_size': 'capacity',
    'size': 'capacity',
    'weight': 'capacity',
    'volume': 'capacity',
    'unit_type': 'unit_type',
    'unit': 'unit_type',
    'min_order_quantity': 'min_order_quantity',
    'moq': 'min_order_quantity',
    'company_name': 'company_name',
    'company': 'company_name',
    'brand': 'company_name',
    'manufacturer': 'company_name',
    'available_stock_count': 'available_stock_count',
    'stock_count': 'available_stock_count',
    'stock': 'available_stock_count',
    'quantity': 'available_stock_count',
    'qty': 'available_stock_count',
    'count': 'available_stock_count',
    'low_stock_threshold': 'low_stock_threshold',
    'threshold': 'low_stock_threshold',
    'sku': 'sku',
    'item_code': 'sku',
    'code': 'sku',
}


def _normalize_row(raw_row: dict) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_val in raw_row.items():
        if not raw_key:
            continue
        clean_key = re.sub(r'[\s\-]+', '_', str(raw_key).strip().lower()).strip('_')
        canonical = _HEADER_ALIASES.get(clean_key, clean_key)
        val = str(raw_val).strip() if raw_val is not None else ''
        if canonical not in normalized or not normalized[canonical]:
            normalized[canonical] = val
    return normalized


def _parse_decimal(val: str, default: str = '0') -> Decimal:
    cleaned = re.sub(r'[^\d.\-]', '', (val or '').strip())
    if not cleaned:
        cleaned = default
    return Decimal(cleaned)


def _parse_int(val: str, default: int = 0) -> int:
    cleaned = re.sub(r'[^\d.\-]', '', (val or '').strip())
    if not cleaned:
        return default
    return int(Decimal(cleaned))


def _decode_csv_text(raw_bytes: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1'):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BusinessRuleError('Could not decode CSV file text encoding. Please save as UTF-8 CSV.')


@transaction.atomic
def import_stock_spreadsheet(*, upload_file, store: StoreDetails, user) -> dict:
    filename = (getattr(upload_file, 'name', '') or '').lower()
    if filename.endswith('.xlsx'):
        rows = _parse_xlsx_rows(upload_file)
    elif filename.endswith('.csv'):
        decoded = _decode_csv_text(upload_file.read())
        sample = decoded[:2048]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(decoded), dialect=dialect))
    else:
        raise BusinessRuleError('Unsupported file format. Please upload a .csv or .xlsx Excel file.')

    if not rows:
        raise BusinessRuleError('The uploaded spreadsheet has no data rows.')

    created_count = 0
    updated_count = 0
    newly_created_products: list[Stock] = []
    errors = []

    for row_num, row in enumerate(rows, start=2):
        norm = _normalize_row(row)
        product_name = norm.get('product_name', '')
        if not product_name:
            errors.append(f'Row {row_num}: missing product_name.')
            continue

        try:
            price = _parse_decimal(norm.get('price', ''), default='0')
            stock_count = _parse_int(norm.get('available_stock_count', ''), default=0)
            moq = _parse_int(norm.get('min_order_quantity', ''), default=1)
            threshold = _parse_int(norm.get('low_stock_threshold', ''), default=10)
            gst_rate = _parse_decimal(norm.get('gst_rate', ''), default='0')
        except Exception as exc:
            errors.append(f'Row {row_num}: invalid numeric value ({exc}).')
            continue

        if price < 0 or stock_count < 0 or moq < 1:
            errors.append(f'Row {row_num}: negative price/stock or invalid MOQ.')
            continue

        sku = norm.get('sku', '')
        unit_type = (norm.get('unit_type') or Stock.UnitTypes.BOX).upper()
        if unit_type not in Stock.UnitTypes.values:
            unit_type = Stock.UnitTypes.BOX

        existing = None
        if sku:
            existing = Stock.objects.select_for_update().filter(store=store, sku=sku).first()
        if existing is None:
            existing = Stock.objects.select_for_update().filter(
                store=store,
                product_name__iexact=product_name,
                capacity=norm.get('capacity', ''),
            ).first()

        if existing:
            old_qty = existing.available_stock_count
            existing.category = norm.get('category', existing.category) or 'General'
            existing.price = price
            existing.gst_rate = gst_rate
            existing.capacity = norm.get('capacity', existing.capacity) or 'Standard'
            existing.unit_type = unit_type
            existing.min_order_quantity = moq
            existing.low_stock_threshold = threshold
            existing.company_name = norm.get('company_name', existing.company_name) or 'Generic'
            existing.available_stock_count = stock_count
            existing.save()
            if old_qty != stock_count:
                ProductAudit.objects.create(
                    product=existing,
                    changed_by=user,
                    old_quantity=old_qty,
                    new_quantity=stock_count,
                    reason=ProductAudit.ReasonClass.STOCK_RESTOCK,
                    notes='CSV import update',
                )
            updated_count += 1
        else:
            stock = Stock.objects.create(
                store=store,
                sku=sku,
                product_name=product_name,
                category=norm.get('category') or 'General',
                price=price,
                gst_rate=gst_rate,
                capacity=norm.get('capacity') or 'Standard',
                unit_type=unit_type,
                min_order_quantity=moq,
                low_stock_threshold=threshold,
                company_name=norm.get('company_name') or 'Generic',
                available_stock_count=stock_count,
            )
            ProductAudit.objects.create(
                product=stock,
                changed_by=user,
                old_quantity=0,
                new_quantity=stock_count,
                reason=ProductAudit.ReasonClass.STOCK_RESTOCK,
                notes='CSV import create',
            )
            newly_created_products.append(stock)
            created_count += 1

    if errors:
        raise BusinessRuleError('; '.join(errors[:5]))

    if newly_created_products:
        notify_previous_customers_new_product(store=store, products=newly_created_products)

    return {'created': created_count, 'updated': updated_count}


def notify_previous_customers_new_product(*, store: StoreDetails, products: list[Stock]) -> int:
    """Notify retailers who previously ordered from this wholesaler when new products arrive."""
    if not products:
        return 0

    User = get_user_model()
    previous_customer_ids = (
        Order.objects.filter(store__owner=store.owner)
        .exclude(status=Order.OrderStatus.CANCELLED)
        .values_list('customer_id', flat=True)
        .distinct()
    )
    customers = list(User.objects.filter(id__in=previous_customer_ids, is_active=True))
    if not customers:
        return 0

    shop_link = reverse('shop_products', kwargs={'store_id': store.store_id})
    created = 0
    for customer in customers:
        if len(products) == 1:
            prod = products[0]
            title = f'New product at {store.store_name}: {prod.product_name}'
            message = (
                f'{store.store_name} added a new product: {prod.product_name} '
                f'({prod.capacity}) at ₹{prod.price}/{prod.get_unit_type_display()}.'
            )
            target_product = prod
        else:
            names = ', '.join(p.product_name for p in products[:3])
            suffix = f' and {len(products) - 3} more' if len(products) > 3 else ''
            title = f'{len(products)} new products arrived at {store.store_name}'
            message = f'{store.store_name} uploaded new stock: {names}{suffix}.'
            target_product = products[0]

        Notification.objects.create(
            recipient=customer,
            store=store,
            product=target_product,
            title=title[:150],
            message=message,
            link=shop_link,
        )
        created += 1
        if customer.email:
            send_mail(
                subject=title,
                message=message,
                from_email=None,
                recipient_list=[customer.email],
                fail_silently=True,
            )
    return created


def import_stock_csv(*, csv_file, store: StoreDetails, user) -> dict:
    return import_stock_spreadsheet(upload_file=csv_file, store=store, user=user)


@transaction.atomic
def create_orders_for_items(
    *,
    retailer,
    items_with_qty: list[tuple[Stock, int]],
    delivery_address: str,
    delivery_city: str,
    delivery_state: str,
    delivery_pincode: str,
    contact_phone: str,
    notes: str = '',
    use_credit: bool = False,
) -> list[Order]:
    """
    Group requested items by wholesaler store and create one Order per store,
    atomically locking & deducting stock and creating audit/shipment records.
    """
    if not items_with_qty:
        raise BusinessRuleError('Cannot place an order with no items.')

    by_store: dict[int, list[tuple[int, int]]] = {}
    for stock, qty in items_with_qty:
        if qty <= 0:
            raise BusinessRuleError(f'Invalid quantity for {stock.product_name}.')
        by_store.setdefault(stock.store_id, []).append((stock.id, qty))

    prepared_orders: list[tuple[StoreDetails, Decimal, int, Decimal, list[tuple[Stock, int, Decimal]]]] = []
    combined_grand_total = Decimal('0.00')

    for store_id, pairs in by_store.items():
        store = StoreDetails.objects.get(id=store_id)
        stock_ids = [p[0] for p in pairs]
        locked_stocks = {
            s.id: s
            for s in Stock.objects.select_for_update().filter(id__in=stock_ids)
        }

        service_area = ServiceArea.objects.filter(
            store=store,
            city__iexact=delivery_city.strip(),
            state__iexact=delivery_state.strip(),
            is_active=True,
        ).first()
        delivery_fee = service_area.delivery_fee if service_area else Decimal('0.00')
        est_days = service_area.estimated_days if service_area else 3

        order_total = Decimal('0.00')
        validated_lines: list[tuple[Stock, int, Decimal]] = []

        for stock_id, qty in pairs:
            stock = locked_stocks[stock_id]
            if qty < stock.min_order_quantity:
                raise BusinessRuleError(
                    f'{stock.product_name} requires a minimum order of {stock.min_order_quantity} {stock.unit_type}.'
                )
            if stock.available_stock_count < qty:
                raise BusinessRuleError(
                    f'Insufficient stock for {stock.product_name}: '
                    f'requested {qty}, available {stock.available_stock_count}.'
                )
            gst_multiplier = Decimal('1.00') + (stock.gst_rate / Decimal('100.00'))
            unit_price_with_tax = (stock.price * gst_multiplier).quantize(Decimal('0.01'))
            order_total += unit_price_with_tax * qty
            validated_lines.append((stock, qty, unit_price_with_tax))

        combined_grand_total += order_total + delivery_fee
        prepared_orders.append((store, order_total, est_days, delivery_fee, validated_lines))

    if use_credit and combined_grand_total > retailer.available_credit:
        raise BusinessRuleError(
            f'Credit limit exceeded: order total ₹{combined_grand_total} exceeds your '
            f'available credit of ₹{retailer.available_credit} (limit ₹{retailer.credit_limit}).'
        )

    created_orders: list[Order] = []

    for store, order_total, est_days, delivery_fee, validated_lines in prepared_orders:
        order = Order.objects.create(
            customer=retailer,
            store=store,
            status=Order.OrderStatus.PENDING,
            payment_status=Order.PaymentStatus.UNPAID,
            total_price=order_total,
            delivery_fee=delivery_fee,
            payment_due_date=(timezone.localdate() + timedelta(days=15)),
            is_credit_order=use_credit,
            delivery_address=delivery_address,
            delivery_city=delivery_city,
            delivery_state=delivery_state,
            delivery_pincode=delivery_pincode,
            contact_phone=contact_phone,
            notes=notes,
        )

        for stock, qty, unit_price in validated_lines:
            OrderItem.objects.create(
                order=order,
                product=stock,
                quantity=qty,
                price_at_purchase=unit_price,
            )
            adjust_stock_quantity(
                stock=stock,
                new_quantity=stock.available_stock_count - qty,
                user=retailer,
                reason=ProductAudit.ReasonClass.ORDER_DEDUCTION,
                order=order,
                notes=f'Order {order.short_id} placed',
            )

        OrderStatusLog.objects.create(
            order=order,
            status=Order.OrderStatus.PENDING,
            changed_by=retailer,
            location_city=delivery_city,
            remarks='Order placed on credit (pay later)' if use_credit else 'Order placed by retailer',
        )

        Shipment.objects.create(
            order=order,
            current_city=store.city,
            current_state=store.state,
            estimated_delivery_date=timezone.localdate() + timedelta(days=est_days),
        )

        created_orders.append(order)

    return created_orders


@transaction.atomic
def create_orders_from_cart(*, retailer, delivery_address: str, delivery_city: str, delivery_state: str, delivery_pincode: str, contact_phone: str, notes: str = '', use_credit: bool = False) -> list[Order]:
    cart = Cart.objects.filter(retailer=retailer).first()
    if not cart or not cart.items.exists():
        raise BusinessRuleError('Your cart is empty.')

    items = [(ci.product, ci.quantity) for ci in cart.items.select_related('product', 'product__store')]
    orders = create_orders_for_items(
        retailer=retailer,
        items_with_qty=items,
        delivery_address=delivery_address,
        delivery_city=delivery_city,
        delivery_state=delivery_state,
        delivery_pincode=delivery_pincode,
        contact_phone=contact_phone,
        notes=notes,
        use_credit=use_credit,
    )
    cart.items.all().delete()
    return orders


@transaction.atomic
def apply_credit_to_order(*, order: Order, retailer) -> Order:
    """Defer payment on an unpaid/partial order using the retailer's ₹10,000 credit limit."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.customer != retailer:
        raise BusinessRuleError('Forbidden.')
    if order.status == Order.OrderStatus.CANCELLED:
        raise BusinessRuleError('Cannot apply credit to a cancelled order.')
    if order.is_fully_paid:
        raise BusinessRuleError('This order is already fully paid.')
    if order.is_credit_order:
        return order

    if order.balance_due > retailer.available_credit:
        raise BusinessRuleError(
            f'Credit limit exceeded: balance due ₹{order.balance_due} exceeds available credit '
            f'₹{retailer.available_credit} (limit ₹{retailer.credit_limit}).'
        )
    order.is_credit_order = True
    order.save(update_fields=['is_credit_order', 'updated_at'])
    return order


@transaction.atomic
def update_order_status(
    *,
    order: Order,
    new_status: str,
    user,
    remarks: str = '',
    location_city: str = '',
):
    order = Order.objects.select_for_update().get(pk=order.pk)
    if new_status not in Order.OrderStatus.values:
        raise BusinessRuleError('Invalid order status.')

    if order.status == Order.OrderStatus.CANCELLED:
        raise BusinessRuleError('Cancelled orders cannot be updated.')
    if order.status == Order.OrderStatus.COMPLETED:
        raise BusinessRuleError('Completed orders cannot be modified.')
    if new_status == Order.OrderStatus.COMPLETED and user != order.customer:
        raise BusinessRuleError('Only the retailer can confirm delivery to mark an order Completed.')
    if order.status == Order.OrderStatus.DELIVERED and new_status not in (
        Order.OrderStatus.DELIVERED,
        Order.OrderStatus.COMPLETED,
    ):
        raise BusinessRuleError('Delivered orders cannot move backwards.')

    old_status = order.status
    if old_status == new_status and not remarks:
        return order

    order.status = new_status
    order.save(update_fields=['status', 'updated_at'])

    if new_status == Order.OrderStatus.CANCELLED and old_status != Order.OrderStatus.CANCELLED:
        for item in order.items.select_related('product'):
            locked_stock = Stock.objects.select_for_update().get(pk=item.product_id)
            adjust_stock_quantity(
                stock=locked_stock,
                new_quantity=locked_stock.available_stock_count + item.quantity,
                user=user,
                reason=ProductAudit.ReasonClass.ORDER_CANCEL_RESTORE,
                order=order,
                notes=f'Order {order.short_id} cancelled',
            )

    shipment, _ = Shipment.objects.get_or_create(order=order)
    if new_status == Order.OrderStatus.SHIPPED and not shipment.dispatched_at:
        shipment.dispatched_at = timezone.now()
    if new_status == Order.OrderStatus.DELIVERED and not shipment.delivered_at:
        shipment.delivered_at = timezone.now()
        shipment.current_city = order.delivery_city or shipment.current_city
        shipment.current_state = order.delivery_state or shipment.current_state
    if location_city:
        shipment.current_city = location_city
    shipment.save()

    OrderStatusLog.objects.create(
        order=order,
        status=new_status,
        changed_by=user,
        location_city=location_city or shipment.current_city,
        remarks=remarks or f'Status updated to {order.get_status_display()}',
    )
    return order


@transaction.atomic
def confirm_order_delivery(*, order: Order, retailer, remarks: str = '') -> Order:
    """Confirm delivery receipt by the retailer, transitioning the order to COMPLETED."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.customer != retailer:
        raise BusinessRuleError('Only the ordering retailer can confirm delivery.')
    if order.status not in (Order.OrderStatus.DELIVERED, Order.OrderStatus.OUT_FOR_DELIVERY):
        raise BusinessRuleError('Delivery can only be confirmed once the order is Out for Delivery or Delivered.')

    now = timezone.now()
    order.status = Order.OrderStatus.COMPLETED
    order.retailer_confirmed_delivery = True
    order.retailer_confirmed_at = now
    order.save(update_fields=['status', 'retailer_confirmed_delivery', 'retailer_confirmed_at', 'updated_at'])

    shipment, _ = Shipment.objects.get_or_create(order=order)
    if not shipment.delivered_at:
        shipment.delivered_at = now
    shipment.current_city = order.delivery_city or shipment.current_city
    shipment.current_state = order.delivery_state or shipment.current_state
    shipment.save()

    OrderStatusLog.objects.create(
        order=order,
        status=Order.OrderStatus.COMPLETED,
        changed_by=retailer,
        location_city=order.delivery_city or shipment.current_city,
        remarks=remarks or 'Delivery confirmed by retailer',
    )
    return order


@transaction.atomic
def recalculate_order_payments(order: Order) -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    verified_total = sum(
        (p.amount for p in order.payments.filter(status=Paymentdetails.VerificationStatus.VERIFIED)),
        Decimal('0.00'),
    )
    order.amount_paid = verified_total
    if verified_total >= order.grand_total and order.grand_total > Decimal('0.00'):
        order.payment_status = Order.PaymentStatus.PAID
    elif verified_total > Decimal('0.00'):
        order.payment_status = Order.PaymentStatus.PARTIAL
    elif order.payment_due_date and timezone.localdate() > order.payment_due_date:
        order.payment_status = Order.PaymentStatus.OVERDUE
    else:
        order.payment_status = Order.PaymentStatus.UNPAID
    order.save(update_fields=['amount_paid', 'payment_status', 'updated_at'])
    return order


@transaction.atomic
def record_payment(
    *,
    order: Order,
    retailer,
    amount: Decimal,
    payment_method: str,
    recorded_by,
    reference_number: str = '',
    proof_file=None,
    notes: str = '',
    auto_verify: bool = False,
) -> Paymentdetails:
    if amount <= Decimal('0.00'):
        raise BusinessRuleError('Payment amount must be greater than zero.')

    # Payment proof screenshot is only accepted/stored for UPI payments
    if payment_method != Paymentdetails.PaymentMethod.UPI:
        proof_file = None

    status = (
        Paymentdetails.VerificationStatus.VERIFIED
        if auto_verify
        else Paymentdetails.VerificationStatus.PENDING
    )
    payment = Paymentdetails.objects.create(
        order=order,
        retailer=retailer,
        amount=amount,
        payment_method=payment_method,
        reference_number=reference_number,
        proof_file=proof_file,
        status=status,
        recorded_by=recorded_by,
        verified_by=recorded_by if auto_verify else None,
        notes=notes,
    )
    if auto_verify:
        recalculate_order_payments(order)
    return payment


@transaction.atomic
def verify_payment(*, payment: Paymentdetails, wholesaler, approved: bool) -> Paymentdetails:
    payment = Paymentdetails.objects.select_for_update().get(pk=payment.pk)
    payment.status = (
        Paymentdetails.VerificationStatus.VERIFIED
        if approved
        else Paymentdetails.VerificationStatus.REJECTED
    )
    payment.verified_by = wholesaler
    payment.save(update_fields=['status', 'verified_by'])
    recalculate_order_payments(payment.order)
    return payment
