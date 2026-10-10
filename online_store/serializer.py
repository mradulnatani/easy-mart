from rest_framework import serializers
from .models import Stock,StoreDetails
from django.contrib.auth import get_user_model
import uuid


User = get_user_model()


class StockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = ['product_name','category','price','availability','capacity','company_name','available_stock_count']

class CSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()

class UserRegistrationSerializer(serializers.ModelSerializer):
    confirm_password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'confirm_password', 'role', 'phone_number', 'shipping_address']
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate(self, data):
        if data['password'] != data['confirm_password']:
            raise serializers.ValidationError({
                "error": "passwords do not match"
            })
        
        if User.objects.filter(email=data['email']).exists():
            raise serializers.ValidationError({
                "error": "Email is already registered, please try another mail address to register"
            })
        if User.objects.filter(username=data['username']).exists():
            raise serializers.ValidationError({
                "error": "Username already exists, please try another username to register"
            })
        return data
        
    def create(self, validated_data):
        validated_data.pop('confirm_password')
        
        user = User(
            username=validated_data['username'],
            email=validated_data['email'],
            role=validated_data['role'],
            phone_number=validated_data['phone_number'],
            shipping_address=validated_data['shipping_address']
        )

        user.set_password(validated_data['password'])
        user.is_active = True
        user.save()
        
        return user


class UserLoginSerializer(serializers.ModelSerializer):
     class Meta:
         model = User
         fields = ['username','password']


     username = serializers.CharField(required=True)
     password = serializers.CharField(required = True, write_only=True)


class UserDetailSerializer(serializers.ModelSerializer):
     model = User
     fields = [
         'id', 'username', 'email', 'first_name', 'last_name', 
         'role', 'phone_number', 'shipping_address', 'is_store_manager', 'is_customer'
     ]

        

class StoreRegistrationSerializer(serializers.ModelSerializer):
    owner_name = serializers.PrimaryKeyRelatedField(
        queryset = User.objects.filter(role='STORE_MANAGER'),
        help_text='select user with the role of Store Manager'
    )
    
    class Meta:
        model = StoreDetails
        fields = ['store_id', 'store_name', 'store_address', 'pin_code', 'owner_name']
        read_only_fields = ['store_id','owner_name'] 

    def validate_pin_code(self, value):
        if len(str(value)) < 5 or len(str(value)) > 6:
            raise serializers.ValidationError("Please enter a valid 5 or 6 digit pin code.")
        return value



