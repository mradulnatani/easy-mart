from rest_framework import serializers
from .models import Stock

class StockSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = '__all__'

class CSVUploadSerializer(serializers.Serializer):
    file = serializers.FileField()