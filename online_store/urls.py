from django.contrib import admin
from django.urls import path
from .views import EnterStockViaCSVView, availableproducts, editstock, RegisterAPIView, LoginAPIView, ProfileAPIView, LogoutAPIView,StoreRegistrationView

urlpatterns = [
    path('uploadviacsv/',EnterStockViaCSVView.as_view(), name="enter_stock_details_via_csv"),
    path('', availableproducts, name="abailable_stock"),
    path('editproduct/<int:pk>/', editstock),
    path('register/', RegisterAPIView.as_view(), name='register'),
    path('login/', LoginAPIView.as_view(), name='login'),
    path('profile/', ProfileAPIView.as_view(), name='profile'),
    path('logout/', LogoutAPIView.as_view(), name='logout'),
    path('registerstore/', StoreRegistrationView.as_view(), name='register_store'),
]
