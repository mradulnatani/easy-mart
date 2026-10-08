from rest_framework import serializers
from .models import Stock

class StockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = {'product_name','category','price','availability','capacity','company_name','available_stock_count'}

class CSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()