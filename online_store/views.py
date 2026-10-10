import csv
import io
from django.shortcuts import render
from rest_framework.views import APIView 
from rest_framework.decorators import api_view
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework import status, generics
from .models import Stock,StoreDetails
from .serializer import StockSerializer,CSVUploadSerializer,UserRegistrationSerializer, UserLoginSerializer, UserDetailSerializer, StoreRegistrationSerializer
from django.shortcuts import get_object_or_404
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import get_user_model



User = get_user_model()

class EnterStockViaCSVView(APIView):
    parser_classes = (MultiPartParser, FormParser)

    serializer_class = CSVUploadSerializer 

    def post(self, request, *args, **kwargs):
        if 'file' not in request.FILES:
            return Response({'error': 'No file uploaded'}, status=status.HTTP_400_BAD_REQUEST)
            
        csv_file = request.FILES['file']

        if not csv_file.name.endswith('.csv'):
            return Response({'error': 'The uploaded file is not a CSV file'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            decoded_file = csv_file.read().decode('utf-8')
            io_string = io.StringIO(decoded_file)
            reader = csv.DictReader(io_string)
            data_list = list(reader)

            serializer = StockSerializer(data=data_list, many=True)

            if serializer.is_valid():
                serializer.save()
                return Response({'message': f'Successfully imported {len(data_list)} records'}, status=status.HTTP_201_CREATED)
            else:
                return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        

@api_view(['GET'])
def availableproducts(request):
    products = Stock.objects.all()
    serializer = StockSerializer(products,many=True)
    return Response(serializer.data,status=status.HTTP_200_OK)


@api_view(['GET','PUT'])
def editstock(request,pk):
    products = get_object_or_404(Stock,pk=pk)
    if request.method == 'GET':
        serializer = StockSerializer(products)
        return Response(serializer.data,status=status.HTTP_200_OK)
    
    elif request.method == 'PUT':
        serializer = StockSerializer(products,data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data,status=status.HTTP_200_OK)
        return Response(serializer.errors,status=status.HTTP_400_BAD_REQUEST)
    



class RegisterAPIView(generics.CreateAPIView):
    serializer_class = UserRegistrationSerializer
    def create(self, request, *args, **kwargs):
        response = super().create(request,*args,**kwargs)
        return Response({
            "message": "Registration successful. You can login now",
            "user": response.data
        }, status = status.HTTP_201_CREATED)
    
class LoginAPIView(generics.GenericAPIView):
    serializer_class = UserLoginSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data['username']
        password = serializer.validated_data['password']

        user = authenticate(username=username, password=password)
        if user:
            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            return Response({
                'refresh': str(refresh),
                'access': str(refresh.access_token),
                'user_id': user.id,
                'username': user.username,
                # Include custom role properties so the frontend knows what UI to render
                'role': user.role,
                'is_store_manager': user.is_store_manager,
                'is_customer': user.is_customer
            }, status=status.HTTP_200_OK)

        return Response({'error': 'Invalid credentials.'}, status=status.HTTP_400_BAD_REQUEST)


class ProfileAPIView(generics.RetrieveUpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserDetailSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        # In JWT, true logout is done by blacklisting the refresh token.
        # Ensure 'rest_framework_simplejwt.token_blacklist' is in INSTALLED_APPS
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({'detail': 'Successfully logged out'}, status=status.HTTP_200_OK)
        except Exception:
            return Response({'error': 'Invalid or missing refresh token.'}, status=status.HTTP_400_BAD_REQUEST)
        

class StoreRegistrationView(generics.CreateAPIView):
    queryset = StoreDetails.objects.all()
    serializer_class = StoreRegistrationSerializer
    permission_classes = [IsAuthenticated]  

    def perform_create(self, serializer):
        serializer.save(owner_name=self.request.user)