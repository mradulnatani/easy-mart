from decimal import Decimal
import uuid

from django.contrib.auth.models import AbstractUser, Group
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class CustomUser(AbstractUser):
    class Roles(models.TextChoices):
        WHOLESALER = 'WHOLESALER', 'Wholesaler'
        RETAILER = 'RETAILER', 'Retailer'

    role = models.CharField(max_length=20, choices=Roles.choices, default=Roles.RETAILER)
    business_name = models.CharField(max_length=150, blank=True, default='')
    gst_number = models.CharField(max_length=20, blank=True, default='')
    phone_number = models.CharField(max_length=15, blank=False, null=False)
    shipping_address = models.CharField(max_length=255, blank=False, null=False)
    city = models.CharField(max_length=80, blank=True, default='')
    state = models.CharField(max_length=80, blank=True, default='')
    pin_code = models.CharField(max_length=10, blank=True, default='')
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('10000.00'))

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)

        if is_new:
            group_name = 'Wholesaler' if self.role == self.Roles.WHOLESALER else 'Retailer'
            group, _ = Group.objects.get_or_create(name=group_name)
            self.groups.add(group)

    @property
    def is_wholesaler(self):
        return self.role == self.Roles.WHOLESALER

    @property
    def is_retailer(self):
        return self.role == self.Roles.RETAILER

    # Backward-compatible aliases
    @property
    def is_store_manager(self):
        return self.is_wholesaler

    @property
    def is_customer(self):
        return self.is_retailer

    @property
    def display_name(self):
        return self.business_name or self.get_full_name() or self.username

    @property
    def credit_used(self):
        active_credit_orders = self.orders.filter(is_credit_order=True).exclude(
            status=Order.OrderStatus.CANCELLED
        )
        return sum((o.balance_due for o in active_credit_orders), Decimal('0.00'))

    @property
    def available_credit(self):
        remaining = self.credit_limit - self.credit_used
        return remaining if remaining > Decimal('0.00') else Decimal('0.00')


class StoreDetails(models.Model):
    store_name = models.CharField(max_length=100)
    store_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    store_address = models.CharField(max_length=255)
    city = models.CharField(max_length=80, blank=True, default='')
    state = models.CharField(max_length=80, blank=True, default='')
    pin_code = models.CharField(max_length=10)
    phone_number = models.CharField(max_length=15, blank=True, default='')
    owner = models.ForeignKey(
        'CustomUser',
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'WHOLESALER'},
        related_name='stores',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        location = f"{self.city}, {self.state}".strip(', ')
        return f"{self.store_name} ({location})" if location else self.store_name


class ServiceArea(models.Model):
    store = models.ForeignKey(StoreDetails, on_delete=models.CASCADE, related_name='service_areas')
    state = models.CharField(max_length=80)
    city = models.CharField(max_length=80)
    pin_code = models.CharField(max_length=10, blank=True, default='')
    estimated_days = models.PositiveIntegerField(default=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('store', 'state', 'city')
        ordering = ['state', 'city']

    def __str__(self):
        return f"{self.store.store_name} -> {self.city}, {self.state}"


class Stock(models.Model):
    class UnitTypes(models.TextChoices):
        BOX = 'BOX', 'Box'
        CARTON = 'CARTON', 'Carton'
        KG = 'KG', 'Kg'
        LITER = 'LITER', 'Liter'
        PIECE = 'PIECE', 'Piece'
        PACK = 'PACK', 'Pack'

    sku = models.CharField(max_length=50, blank=True, default='')
    product_name = models.CharField(max_length=100)
    category = models.CharField(max_length=50)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    availability = models.BooleanField(default=True)
    capacity = models.CharField(max_length=80)
    unit_type = models.CharField(max_length=20, choices=UnitTypes.choices, default=UnitTypes.BOX)
    min_order_quantity = models.PositiveIntegerField(default=1)
    company_name = models.CharField(max_length=100)
    available_stock_count = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=10)
    store = models.ForeignKey('StoreDetails', on_delete=models.CASCADE, related_name='stocks')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['product_name', 'id']

    def save(self, *args, **kwargs):
        if not self.sku:
            self.sku = f"SKU-{uuid.uuid4().hex[:8].upper()}"
        self.availability = self.available_stock_count > 0
        super().save(*args, **kwargs)

    @property
    def is_low_stock(self):
        return 0 < self.available_stock_count <= self.low_stock_threshold

    def __str__(self):
        return f"{self.product_name} ({self.sku}) - {self.available_stock_count} {self.unit_type}"


class Cart(models.Model):
    retailer = models.OneToOneField(
        'CustomUser',
        on_delete=models.CASCADE,
        related_name='cart',
    )
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def total_amount(self):
        return sum((item.line_total for item in self.items.select_related('product')), Decimal('0.00'))

    @property
    def total_items(self):
        return sum(item.quantity for item in self.items.all())

    def __str__(self):
        return f"Cart ({self.retailer.username})"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('cart', 'product')

    @property
    def line_total(self):
        return self.product.price * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.product.product_name}"


class Order(models.Model):
    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        CONFIRMED = 'CONFIRMED', 'Confirmed'
        PACKED = 'PACKED', 'Packed'
        SHIPPED = 'SHIPPED', 'Shipped'
        IN_TRANSIT = 'IN_TRANSIT', 'In Transit'
        OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY', 'Out for Delivery'
        DELIVERED = 'DELIVERED', 'Delivered (Awaiting Retailer Confirmation)'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class PaymentStatus(models.TextChoices):
        UNPAID = 'UNPAID', 'Unpaid'
        PARTIAL = 'PARTIAL', 'Partially Paid'
        PAID = 'PAID', 'Paid'
        OVERDUE = 'OVERDUE', 'Overdue'

    order_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(
        'CustomUser',
        on_delete=models.CASCADE,
        limit_choices_to={'role': 'RETAILER'},
        related_name='orders',
    )
    store = models.ForeignKey(
        'StoreDetails',
        to_field='store_id',
        on_delete=models.CASCADE,
        related_name='orders',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=25,
        choices=OrderStatus.choices,
        default=OrderStatus.PENDING,
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.UNPAID,
    )
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    payment_due_date = models.DateField(null=True, blank=True)
    is_credit_order = models.BooleanField(default=False)
    retailer_confirmed_delivery = models.BooleanField(default=False)
    retailer_confirmed_at = models.DateTimeField(null=True, blank=True)

    delivery_address = models.CharField(max_length=255, blank=True, default='')
    delivery_city = models.CharField(max_length=80, blank=True, default='')
    delivery_state = models.CharField(max_length=80, blank=True, default='')
    delivery_pincode = models.CharField(max_length=10, blank=True, default='')
    contact_phone = models.CharField(max_length=15, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at']

    @property
    def grand_total(self):
        return self.total_price + self.delivery_fee

    @property
    def balance_due(self):
        if self.status == self.OrderStatus.CANCELLED:
            return Decimal('0.00')
        remaining = self.grand_total - self.amount_paid
        return remaining if remaining > Decimal('0.00') else Decimal('0.00')

    @property
    def is_fully_paid(self):
        return self.balance_due <= Decimal('0.00')

    @property
    def has_unsettled_credit(self):
        return (
            self.is_credit_order
            and not self.is_fully_paid
            and self.status != self.OrderStatus.CANCELLED
        )

    @property
    def can_manage_payments(self):
        return self.status != self.OrderStatus.CANCELLED and (
            self.status != self.OrderStatus.COMPLETED or self.has_unsettled_credit
        )

    @property
    def short_id(self):
        return str(self.order_id)[:8].upper()

    def __str__(self):
        return f"Order {self.short_id} - {self.customer.username} ({self.status})"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Stock,
        on_delete=models.PROTECT,
        related_name='order_items',
    )
    quantity = models.PositiveIntegerField(default=1)
    fulfilled_quantity = models.PositiveIntegerField(default=0)
    price_at_purchase = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def line_total(self):
        return self.price_at_purchase * self.quantity

    def __str__(self):
        return f"{self.quantity} x {self.product.product_name} (Order: {self.order.short_id})"


class OrderStatusLog(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='status_logs')
    status = models.CharField(max_length=25, choices=Order.OrderStatus.choices)
    changed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_status_changes',
    )
    location_city = models.CharField(max_length=80, blank=True, default='')
    remarks = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.order.short_id}: {self.status}"


class Shipment(models.Model):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='shipment')
    tracking_number = models.CharField(max_length=80, blank=True, default='')
    transport_company = models.CharField(max_length=120, blank=True, default='')
    driver_name = models.CharField(max_length=100, blank=True, default='')
    driver_phone = models.CharField(max_length=15, blank=True, default='')
    vehicle_number = models.CharField(max_length=30, blank=True, default='')
    current_city = models.CharField(max_length=80, blank=True, default='')
    current_state = models.CharField(max_length=80, blank=True, default='')
    dispatched_at = models.DateTimeField(null=True, blank=True)
    estimated_delivery_date = models.DateField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    delivery_notes = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Shipment for Order {self.order.short_id} ({self.tracking_number or 'Pending'})"


class ProductReview(models.Model):
    product = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='product_review')
    customer = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='product_review_customer')
    rating = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)


class ProductAudit(models.Model):
    class ReasonClass(models.TextChoices):
        STOCK_RESTOCK = 'STOCK_RESTOCK', 'Stock Restock'
        ORDER_DEDUCTION = 'ORDER_DEDUCTION', 'Order Deduction'
        ORDER_CANCEL_RESTORE = 'ORDER_CANCEL_RESTORE', 'Order Cancel Restore'
        DISCREPANCY_CORRECTION = 'DISCREPANCY_CORRECTION', 'Discrepancy Correction'
        DAMAGED_GOODS = 'DAMAGED_GOODS', 'Damaged Goods'

    product = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='audited_product')
    changed_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='changed_by_user',
    )
    order = models.ForeignKey(
        Order,
        to_field='order_id',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='order_audits',
    )
    old_quantity = models.IntegerField()
    new_quantity = models.IntegerField()
    timestamp = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(max_length=40, choices=ReasonClass.choices, default=ReasonClass.STOCK_RESTOCK)
    notes = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.product.product_name}: {self.old_quantity} -> {self.new_quantity} ({self.reason})"


class Paymentdetails(models.Model):
    class PaymentMethod(models.TextChoices):
        UPI = 'UPI', 'UPI'
        BANK_TRANSFER = 'BANK_TRANSFER', 'NEFT / RTGS / IMPS'
        CHEQUE = 'CHEQUE', 'Cheque'
        CASH = 'CASH', 'Cash'
        CREDIT = 'CREDIT', 'Credit Settlement'

    class VerificationStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending Verification'
        VERIFIED = 'VERIFIED', 'Verified'
        REJECTED = 'REJECTED', 'Rejected'

    payment_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    retailer = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='retailer_payments',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.UPI)
    reference_number = models.CharField(max_length=100, blank=True, default='')
    proof_file = models.FileField(upload_to='payment_proofs/', null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    recorded_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='recorded_payments',
    )
    verified_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_payments',
    )
    payment_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-payment_date']

    def __str__(self):
        return f"Payment {str(self.payment_id)[:8].upper()} - {self.amount} ({self.status})"


class Notification(models.Model):
    recipient = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    store = models.ForeignKey(
        StoreDetails,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications',
    )
    product = models.ForeignKey(
        Stock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
    )
    title = models.CharField(max_length=150)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True, default='')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient.username}: {self.title}"
