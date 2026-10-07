from django.db import models

class Stock(models.Model):
    product_name = models.CharField(max_length=100)
    category = models.CharField(max_length=50)
    price = models.FloatField()
    availability = models.BooleanField(default=False)
    capacity = models.CharField(max_length=80)
    company_name = models.CharField(max_length=100)
# Create your models here.
