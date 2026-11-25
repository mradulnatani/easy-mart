from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
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

User = get_user_model()


class StockSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source='store.store_name', read_only=True)
    store_uuid = serializers.UUIDField(source='store.store_id', read_only=True)
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = Stock
        fields = [
            'id',
            'sku',
            'product_name',
            'category',
            'price',
            'gst_rate',
            'availability',
            'capacity',
            'unit_type',
            'min_order_quantity',
            'company_name',
            'available_stock_count',
            'low_stock_threshold',
            'is_low_stock',
            'store',
            'store_name',
            'store_uuid',
            'updated_at',
        ]
        read_only_fields = ['id', 'availability', 'store_name', 'store_uuid', 'is_low_stock', 'updated_at']


class CSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    store_id = serializers.UUIDField(required=False)


class UserRegistrationSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True)
    role = serializers.CharField(default=User.Roles.RETAILER)

    class Meta:
        model = User
        fields = [
            'username',
            'email',
            'password',
            'confirm_password',
            'role',
            'business_name',
            'gst_number',
            'phone_number',
            'shipping_address',
            'city',
            'state',
            'pin_code',
        ]
        extra_kwargs = {
            'password': {'write_only': True},
        }

    def validate_role(self, value):
        normalized = (value or '').strip().upper()
        alias_map = {
            'STORE_MANAGER': User.Roles.WHOLESALER,
            'WHOLESALER': User.Roles.WHOLESALER,
            'CUSTOMER': User.Roles.RETAILER,
            'RETAILER': User.Roles.RETAILER,
        }
        if normalized not in alias_map:
            raise serializers.ValidationError('Role must be WHOLESALER or RETAILER.')
        return alias_map[normalized]

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({'confirm_password': 'Passwords do not match.'})

        if data.get('email') and User.objects.filter(email__iexact=data['email']).exists():
            raise serializers.ValidationError({'email': 'Email is already registered.'})

        if User.objects.filter(username__iexact=data['username']).exists():
            raise serializers.ValidationError({'username': 'Username already exists.'})
        return data

    def create(self, validated_data):
        from .services import ensure_default_store_for_wholesaler

        validated_data.pop('confirm_password')
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.is_active = True
        user.save()
        if user.is_wholesaler:
            ensure_default_store_for_wholesaler(user)
        return user


class UserLoginSerializer(serializers.Serializer):
    username = serializers.CharField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class UserDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id',
            'username',
            'email',
            'first_name',
            'last_name',
            'role',
            'business_name',
            'gst_number',
            'phone_number',
            'shipping_address',
            'city',
            'state',
            'pin_code',
            'credit_limit',
            'is_wholesaler',
            'is_retailer',
            'credit_used',
            'available_credit',
        ]
        read_only_fields = [
            'id', 'username', 'role', 'credit_limit', 'is_wholesaler',
            'is_retailer', 'credit_used', 'available_credit',
        ]


class ServiceAreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceArea
        fields = ['id', 'store', 'state', 'city', 'pin_code', 'estimated_days', 'delivery_fee', 'is_active']
        read_only_fields = ['id', 'store']


class StoreRegistrationSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    service_areas = ServiceAreaSerializer(many=True, read_only=True)

    class Meta:
        model = StoreDetails
        fields = [
            'id',
            'store_id',
            'store_name',
            'store_address',
            'city',
            'state',
            'pin_code',
            'phone_number',
            'owner',
            'owner_username',
            'service_areas',
            'created_at',
        ]
        read_only_fields = ['id', 'store_id', 'owner', 'owner_username', 'created_at']

    def validate_pin_code(self, value):
        cleaned = str(value).strip()
        if not cleaned.isdigit() or len(cleaned) not in (5, 6):
            raise serializers.ValidationError('Please enter a valid 5 or 6 digit pin code.')
        return cleaned


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.product_name', read_only=True)
    sku = serializers.CharField(source='product.sku', read_only=True)
    unit_type = serializers.CharField(source='product.unit_type', read_only=True)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'product', 'sku', 'product_name', 'unit_type', 'quantity', 'fulfilled_quantity', 'price_at_purchase', 'line_total']


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = [
            'tracking_number',
            'transport_company',
            'driver_name',
            'driver_phone',
            'vehicle_number',
            'current_city',
            'current_state',
            'dispatched_at',
            'estimated_delivery_date',
            'delivered_at',
            'delivery_notes',
            'updated_at',
        ]


class OrderStatusLogSerializer(serializers.ModelSerializer):
    changed_by_username = serializers.CharField(source='changed_by.username', read_only=True)

    class Meta:
        model = OrderStatusLog
        fields = ['id', 'status', 'changed_by_username', 'location_city', 'remarks', 'created_at']


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    shipment = ShipmentSerializer(read_only=True)
    status_logs = OrderStatusLogSerializer(many=True, read_only=True)
    store_name = serializers.CharField(source='store.store_name', read_only=True)
    customer_username = serializers.CharField(source='customer.username', read_only=True)
    grand_total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = [
            'id',
            'order_id',
            'customer',
            'customer_username',
            'store',
            'store_name',
            'status',
            'payment_status',
            'total_price',
            'delivery_fee',
            'grand_total',
            'amount_paid',
            'balance_due',
            'payment_due_date',
            'is_credit_order',
            'retailer_confirmed_delivery',
            'retailer_confirmed_at',
            'delivery_address',
            'delivery_city',
            'delivery_state',
            'delivery_pincode',
            'contact_phone',
            'notes',
            'items',
            'shipment',
            'status_logs',
            'created_at',
            'updated_at',
        ]


class OrderCreateItemInputSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)


class OrderCreateSerializer(serializers.Serializer):
    items = OrderCreateItemInputSerializer(many=True, min_length=1)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    delivery_city = serializers.CharField(required=False, allow_blank=True)
    delivery_state = serializers.CharField(required=False, allow_blank=True)
    delivery_pincode = serializers.CharField(required=False, allow_blank=True)
    contact_phone = serializers.CharField(required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    use_credit = serializers.BooleanField(required=False, default=False)


class PaymentSerializer(serializers.ModelSerializer):
    order_uuid = serializers.UUIDField(source='order.order_id', read_only=True)
    retailer_username = serializers.CharField(source='retailer.username', read_only=True)

    class Meta:
        model = Paymentdetails
        fields = [
            'id',
            'payment_id',
            'order',
            'order_uuid',
            'retailer',
            'retailer_username',
            'amount',
            'payment_method',
            'reference_number',
            'proof_file',
            'status',
            'payment_date',
            'notes',
        ]
        read_only_fields = ['id', 'payment_id', 'retailer', 'status', 'payment_date']


class ProductAuditSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.product_name', read_only=True)
    changed_by_username = serializers.CharField(source='changed_by.username', read_only=True)

    class Meta:
        model = ProductAudit
        fields = [
            'id',
            'product',
            'product_name',
            'changed_by_username',
            'order',
            'old_quantity',
            'new_quantity',
            'reason',
            'notes',
            'timestamp',
        ]


class NotificationSerializer(serializers.ModelSerializer):
    store_name = serializers.CharField(source='store.store_name', read_only=True)

    class Meta:
        model = Notification
        fields = ['id', 'store', 'store_name', 'product', 'title', 'message', 'link', 'is_read', 'created_at']
