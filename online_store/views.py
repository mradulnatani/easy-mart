import csv
import io
from django.shortcuts import render
from rest_framework.views import APIView 
from rest_framework.decorators import api_view
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework import status
from .models import Stock
from .serializer import StockSerializer,CSVUploadSerializer
from django.shortcuts import get_object_or_404

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
