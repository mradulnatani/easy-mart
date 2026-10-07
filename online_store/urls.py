from django.contrib import admin
from django.urls import path
from .views import EnterStockViaCSVView, availableproducts, editstock

urlpatterns = [
    path('uploadviacsv/',EnterStockViaCSVView.as_view(), name="enter_stock_details_via_csv"),
    path('', availableproducts, name="abailable_stock"),
    path('editproduct/<int:pk>/', editstock),
]
