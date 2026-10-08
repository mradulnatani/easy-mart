from django.db import models
from django.contrib.auth.models import AbstractUser, Group
import uuid

class Stock(models.Model):
    product_name = models.CharField(max_length=100)
    category = models.CharField(max_length=50)
    price = models.FloatField()
    availability = models.BooleanField(default=False)
    capacity = models.CharField(max_length=80)
    company_name = models.CharField(max_length=100)
    available_stock_count = models.IntegerField()
    store = models.ForeignKey('StoreDetails', to_field='store_id', on_delete=models.CASCADE)


class CustomUSer(AbstractUser):
    class Roles(models.TextChoices):
        STORE_MANAGER = 'STORE_MANAGER', 'Store Manager'
        CUSTOMER = 'CUSTOMER', 'Customer'

    
    role = models.CharField(max_length=20,choices=Roles.choices,default=Roles.CUSTOMER)

    phone_number = models.CharField(max_length=10,blank=False,null=False)
    shipping_address = models.CharField(max_length=200,blank=False,null=False)


    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if is_new:
            if self.role == self.Roles.STORE_MANAGER:
                group, _ = Group.objects.get_or_create(name='Store Manager')
                self.groups.add(group)
            elif self.role == self.Roles.CUSTOMER:
                group,_ = Group.objects.get_or_create(name='Customer')
                self.groups.add(group)
    
    @property
    def is_store_manager(self):
        return self.role == self.Roles.STORE_MANAGER
    
    @property
    def is_customer(self):
        return self.role == self.Roles.CUSTOMER
    


class StoreDetails(models.Model):
    store_name = models.CharField(max_length=100)
    store_id = models.UUIDField(default=uuid.uuid4,editable=False)
    store_address = models.CharField(max_length=200)
    pin_code = models.IntegerField()
    owner_name = models.ForeignKey('CustomUSer',on_delete=models.CASCADE,limit_choices_to={'role':'STORE_MANAGER'})



class Order(models.Model):
    class OrderStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        SHIPPED = 'SHIPPED', 'Shipped'
        DELIVERED = 'DELIVERED', 'Delivered'
        CANCELLED = 'CANCELLED', 'Cancelled'

    order_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    
    customer = models.ForeignKey(
        'CustomUSer', 
        on_delete=models.CASCADE, 
        limit_choices_to={'role': 'CUSTOMER'},
        related_name='orders'
    )
    
    store = models.ForeignKey(
        'StoreDetails', 
        to_field='store_id', 
        on_delete=models.CASCADE,
        related_name='orders'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(
        max_length=20, 
        choices=OrderStatus.choices, 
        default=OrderStatus.PENDING
    )
    total_price = models.FloatField(default=0.0)

    def __str__(self):
        return f"Order {self.order_id} - {self.customer.username} ({self.status})"



class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, 
        on_delete=models.CASCADE, 
        related_name='items'
    )
    
    product = models.ForeignKey(
        Stock, 
        on_delete=models.PROTECT, 
        related_name='order_items'
    )
    
    quantity = models.PositiveIntegerField(default=1)
    
    price_at_purchase = models.FloatField()

    def __str__(self):
        return f"{self.quantity} x {self.product.product_name} (Order: {self.order.id})"
