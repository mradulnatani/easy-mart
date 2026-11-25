from decimal import Decimal
import io
import zipfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    Notification,
    Order,
    Paymentdetails,
    ProductAudit,
    ServiceArea,
    Stock,
    StoreDetails,
)
from .qr import generate_qr_matrix, generate_qr_png, generate_qr_svg

User = get_user_model()


class EasyMartEndToEndTests(TestCase):
    def setUp(self):
        self.wholesaler = User.objects.create_user(
            username='wholesaler1',
            email='w1@example.com',
            password='StrongPassword123!',
            role=User.Roles.WHOLESALER,
            business_name='Apex Wholesale Depot',
            phone_number='9876543210',
            shipping_address='12 Industrial Estate',
            city='Ahmedabad',
            state='Gujarat',
            pin_code='380001',
        )
        self.other_wholesaler = User.objects.create_user(
            username='wholesaler2',
            email='w2@example.com',
            password='StrongPassword123!',
            role=User.Roles.WHOLESALER,
            business_name='Metro Supplies',
            phone_number='9876543211',
            shipping_address='44 Market Road',
            city='Mumbai',
            state='Maharashtra',
            pin_code='400001',
        )
        self.retailer = User.objects.create_user(
            username='retailer1',
            email='r1@example.com',
            password='StrongPassword123!',
            role=User.Roles.RETAILER,
            business_name='City Mart Retail',
            phone_number='9123456789',
            shipping_address='88 MG Road',
            city='Pune',
            state='Maharashtra',
            pin_code='411001',
        )
        self.store = StoreDetails.objects.create(
            store_name='Apex Main Hub',
            store_address='12 Industrial Estate',
            city='Ahmedabad',
            state='Gujarat',
            pin_code='380001',
            phone_number='9876543210',
            owner=self.wholesaler,
        )
        self.other_store = StoreDetails.objects.create(
            store_name='Metro Hub',
            store_address='44 Market Road',
            city='Mumbai',
            state='Maharashtra',
            pin_code='400001',
            owner=self.other_wholesaler,
        )
        ServiceArea.objects.create(
            store=self.store,
            state='Maharashtra',
            city='Pune',
            estimated_days=2,
            delivery_fee=Decimal('150.00'),
        )
        self.stock = Stock.objects.create(
            store=self.store,
            sku='SUGAR-50',
            product_name='Refined Sugar',
            category='Groceries',
            price=Decimal('2000.00'),
            gst_rate=Decimal('5.00'),
            capacity='50kg Bag',
            unit_type=Stock.UnitTypes.BAG if hasattr(Stock.UnitTypes, 'BAG') else Stock.UnitTypes.PACK,
            min_order_quantity=2,
            company_name='SweetCo',
            available_stock_count=20,
            low_stock_threshold=5,
        )
        self.other_stock = Stock.objects.create(
            store=self.other_store,
            sku='OIL-15',
            product_name='Sunflower Oil',
            category='Oils',
            price=Decimal('1800.00'),
            capacity='15L Tin',
            company_name='SunCo',
            available_stock_count=15,
        )

    def test_qr_generator_outputs_valid_matrix_svg_and_png(self):
        url = f'http://testserver/store/shop/{self.store.store_id}/'
        matrix = generate_qr_matrix(url)
        size = len(matrix)
        self.assertGreaterEqual(size, 21)
        # Verify top-left 7x7 finder pattern outer ring is dark
        self.assertTrue(all(matrix[0][c] for c in range(7)))
        self.assertTrue(all(matrix[6][c] for c in range(7)))
        self.assertTrue(matrix[3][3])  # center module of finder pattern

        svg = generate_qr_svg(url)
        self.assertIn('<svg ', svg)
        self.assertIn('<path ', svg)

        png = generate_qr_png(url)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_wholesaler_qr_download_and_scan_redirect_to_products(self):
        # Login via HTML form to obtain JWT cookies
        login_resp = self.client.post(
            reverse('login_page'),
            {'username': 'wholesaler1', 'password': 'StrongPassword123!'},
        )
        self.assertEqual(login_resp.status_code, 302)
        self.assertIn('access_token', login_resp.cookies)

        qr_page = self.client.get(reverse('wholesaler_store_qr', kwargs={'store_id': self.store.store_id}))
        self.assertEqual(qr_page.status_code, 200)
        self.assertContains(qr_page, self.store.store_name)
        self.assertContains(qr_page, '<svg')

        png_resp = self.client.get(
            reverse('wholesaler_store_qr_download', kwargs={'store_id': self.store.store_id}) + '?format=png'
        )
        self.assertEqual(png_resp.status_code, 200)
        self.assertEqual(png_resp['Content-Type'], 'image/png')
        self.assertTrue(png_resp.content.startswith(b'\x89PNG\r\n\x1a\n'))

        svg_resp = self.client.get(
            reverse('wholesaler_store_qr_download', kwargs={'store_id': self.store.store_id}) + '?format=svg'
        )
        self.assertEqual(svg_resp.status_code, 200)
        self.assertEqual(svg_resp['Content-Type'], 'image/svg+xml')
        self.assertIn(b'<svg ', svg_resp.content)

        # Logout and simulate an anonymous customer/retailer scanning the QR URL
        self.client.get(reverse('logout_page'))
        shop_resp = self.client.get(reverse('shop_products', kwargs={'store_id': self.store.store_id}))
        self.assertEqual(shop_resp.status_code, 200)
        self.assertContains(shop_resp, 'Refined Sugar')
        self.assertNotContains(shop_resp, 'Sunflower Oil')

    def test_cross_role_and_cross_store_permissions(self):
        api = APIClient()
        # Unauthenticated upload denied
        resp = api.post(reverse('enter_stock_details_via_csv'))
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

        # Retailer cannot edit wholesaler stock
        api.force_authenticate(user=self.retailer)
        resp = api.put(reverse('edit_stock', kwargs={'pk': self.stock.pk}), {'available_stock_count': 99})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

        # Wholesaler2 cannot edit Wholesaler1's stock
        api.force_authenticate(user=self.other_wholesaler)
        resp = api.patch(reverse('edit_stock', kwargs={'pk': self.stock.pk}), {'available_stock_count': 99})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_csv_upload_and_audit_log(self):
        api = APIClient()
        api.force_authenticate(user=self.wholesaler)

        csv_content = (
            "sku,product_name,category,price,gst_rate,capacity,unit_type,min_order_quantity,company_name,available_stock_count\n"
            "SUGAR-50,Refined Sugar,Groceries,2100.00,5.00,50kg Bag,PACK,2,SweetCo,35\n"
            "TEA-10,Assam Tea,Beverages,1500.00,5.00,10kg Carton,CARTON,1,AssamGold,12\n"
        ).encode('utf-8')
        upload = SimpleUploadedFile('inventory.csv', csv_content, content_type='text/csv')
        resp = api.post(
            reverse('enter_stock_details_via_csv'),
            {'file': upload, 'store_id': str(self.store.store_id)},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['created'], 1)
        self.assertEqual(resp.data['updated'], 1)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available_stock_count, 35)
        self.assertEqual(ProductAudit.objects.filter(product=self.stock).count(), 1)

    def test_xlsx_excel_upload_api_and_web_view(self):
        # Build a valid .xlsx workbook in memory (sharedStrings + sheet1)
        shared_strings = [
            'sku', 'product_name', 'category', 'price', 'capacity',
            'company_name', 'available_stock_count',
            'SUGAR-50', 'Refined Sugar', 'Groceries', '50kg Bag', 'SweetCo',
            'DAL-30', 'Toor Dal', 'Pulses', '30kg Bag', 'FarmFresh',
        ]
        ss_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + ''.join(f'<si><t>{s}</t></si>' for s in shared_strings)
            + '</sst>'
        )
        sheet_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1">'
            '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
            '<c r="C1" t="s"><v>2</v></c><c r="D1" t="s"><v>3</v></c>'
            '<c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>5</v></c>'
            '<c r="G1" t="s"><v>6</v></c>'
            '</row>'
            '<row r="2">'
            '<c r="A2" t="s"><v>7</v></c><c r="B2" t="s"><v>8</v></c>'
            '<c r="C2" t="s"><v>9</v></c><c r="D2"><v>2200</v></c>'
            '<c r="E2" t="s"><v>10</v></c><c r="F2" t="s"><v>11</v></c>'
            '<c r="G2"><v>50</v></c>'
            '</row>'
            '<row r="3">'
            '<c r="A3" t="s"><v>12</v></c><c r="B3" t="s"><v>13</v></c>'
            '<c r="C3" t="s"><v>14</v></c><c r="D3"><v>3600</v></c>'
            '<c r="E3" t="s"><v>15</v></c><c r="F3" t="s"><v>16</v></c>'
            '<c r="G3"><v>18</v></c>'
            '</row>'
            '</sheetData></worksheet>'
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as zf:
            zf.writestr('xl/sharedStrings.xml', ss_xml)
            zf.writestr('xl/worksheets/sheet1.xml', sheet_xml)
        xlsx_bytes = buf.getvalue()

        # Test via Wholesaler HTML Inventory view
        self.client.post(
            reverse('login_page'),
            {'username': 'wholesaler1', 'password': 'StrongPassword123!'},
        )
        upload = SimpleUploadedFile(
            'stock.xlsx',
            xlsx_bytes,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        web_resp = self.client.post(
            reverse('wholesaler_inventory'),
            {
                'action': 'upload_spreadsheet',
                'store_id': str(self.store.store_id),
                'file': upload,
            },
        )
        self.assertEqual(web_resp.status_code, 302)

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available_stock_count, 50)
        self.assertEqual(self.stock.price, Decimal('2200.00'))
        dal = Stock.objects.get(store=self.store, sku='DAL-30')
        self.assertEqual(dal.product_name, 'Toor Dal')
        self.assertEqual(dal.available_stock_count, 18)

    def test_wholesaler_registration_autostore_flexible_csv_and_retailer_visibility(self):
        # Register a new wholesaler via HTML form
        reg_resp = self.client.post(
            reverse('register_page'),
            {
                'role': User.Roles.WHOLESALER,
                'business_name': 'Parag Traders',
                'username': 'parag_w',
                'email': 'parag@example.com',
                'phone_number': '9302956451',
                'shipping_address': '4 Suraj Nagar',
                'city': 'Ujjain',
                'state': 'Madhya Pradesh',
                'pin_code': '456001',
                'password': 'StrongPassword123!',
                'confirm_password': 'StrongPassword123!',
            },
        )
        self.assertEqual(reg_resp.status_code, 302)
        new_wholesaler = User.objects.get(username='parag_w')
        auto_store = StoreDetails.objects.get(owner=new_wholesaler)
        self.assertEqual(auto_store.store_name, 'Parag Traders')

        # Upload CSV with flexible human-friendly headers, currency symbol, and float qty (no target store needed)
        csv_bytes = (
            "Product Name,Category,Rate,Size,Brand,Qty\n"
            "Basmati Rice,Grains,\"₹1,450.00\",25kg Bag,Royal,30.0\n"
        ).encode('utf-8')
        upload = SimpleUploadedFile('products.csv', csv_bytes, content_type='text/csv')
        inv_resp = self.client.post(
            reverse('wholesaler_inventory'),
            {
                'action': 'upload_spreadsheet',
                'file': upload,
            },
            follow=True,
        )
        self.assertEqual(inv_resp.status_code, 200)
        self.assertTrue(Stock.objects.filter(store=auto_store, product_name='Basmati Rice', available_stock_count=30).exists())

        # Retailer logs in and sees the new wholesale store on dashboard & shops page
        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        dash_resp = self.client.get(reverse('retailer_dashboard'))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertContains(dash_resp, 'Parag Traders')

        shops_resp = self.client.get(reverse('retailer_shops'))
        self.assertEqual(shops_resp.status_code, 200)
        self.assertContains(shops_resp, 'Parag Traders')

    def test_upi_only_proof_delivery_confirmation_credit_limit_and_notifications(self):
        # Verify default ₹10,000 credit limit
        self.assertEqual(self.retailer.credit_limit, Decimal('10000.00'))
        self.assertEqual(self.retailer.available_credit, Decimal('10000.00'))

        # Create a second retailer who has NOT ordered from self.store
        non_customer = User.objects.create_user(
            username='retailer_other',
            email='rother@example.com',
            password='StrongPassword123!',
            role=User.Roles.RETAILER,
            phone_number='9111222333',
            shipping_address='10 Park St',
        )

        # Retailer places a 4-bag order on credit (4 * 2000 * 1.05 + 150 = 8550.00 <= 10000)
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        self.client.post(
            reverse('add_to_cart', kwargs={'product_id': self.stock.id}),
            {'quantity': 4},
        )
        self.client.post(
            reverse('retailer_cart'),
            {
                'action': 'checkout',
                'delivery_address': '88 MG Road',
                'delivery_city': 'Pune',
                'delivery_state': 'Maharashtra',
                'delivery_pincode': '411001',
                'contact_phone': '9123456789',
                'payment_choice': 'credit',
            },
        )
        order = Order.objects.get(customer=self.retailer)
        self.assertTrue(order.is_credit_order)
        self.assertEqual(order.grand_total, Decimal('8550.00'))
        credit_detail = self.client.get(reverse('retailer_order_detail', kwargs={'order_id': order.order_id}))
        self.assertContains(credit_detail, 'No payment type or proof of payment is required right now')
        self.assertNotContains(credit_detail, 'Submit Payment Details')
        self.retailer.refresh_from_db()
        self.assertEqual(self.retailer.credit_used, Decimal('8550.00'))
        self.assertEqual(self.retailer.available_credit, Decimal('1450.00'))

        # Attempting another credit order exceeding remaining ₹1,450 fails cleanly
        self.client.post(
            reverse('add_to_cart', kwargs={'product_id': self.stock.id}),
            {'quantity': 2},
        )
        over_resp = self.client.post(
            reverse('retailer_cart'),
            {
                'action': 'checkout',
                'delivery_address': '88 MG Road',
                'delivery_city': 'Pune',
                'delivery_state': 'Maharashtra',
                'delivery_pincode': '411001',
                'contact_phone': '9123456789',
                'payment_choice': 'credit',
            },
            follow=True,
        )
        self.assertContains(over_resp, 'Credit limit exceeded')
        self.assertEqual(Order.objects.filter(customer=self.retailer).count(), 1)

        # Payment proof is ONLY saved when payment_method is UPI (ignored for CASH)
        non_upi_proof = SimpleUploadedFile('cash.png', b'fakeimg', content_type='image/png')
        self.client.post(
            reverse('retailer_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'submit_payment',
                'amount': '500.00',
                'payment_method': Paymentdetails.PaymentMethod.CASH,
                'proof_file': non_upi_proof,
            },
        )
        cash_payment = Paymentdetails.objects.get(order=order, payment_method=Paymentdetails.PaymentMethod.CASH)
        self.assertFalse(bool(cash_payment.proof_file))

        upi_proof = SimpleUploadedFile('upi.png', b'fakeimg', content_type='image/png')
        self.client.post(
            reverse('retailer_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'submit_payment',
                'amount': '1000.00',
                'payment_method': Paymentdetails.PaymentMethod.UPI,
                'reference_number': 'UPI12345',
                'proof_file': upi_proof,
            },
        )
        upi_payment = Paymentdetails.objects.get(order=order, payment_method=Paymentdetails.PaymentMethod.UPI)
        self.assertTrue(bool(upi_payment.proof_file))

        # Wholesaler marks order DELIVERED (and cannot bypass retailer to mark COMPLETED)
        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'wholesaler1', 'password': 'StrongPassword123!'},
        )
        bad_complete = self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'update_status', 'status': Order.OrderStatus.COMPLETED},
            follow=True,
        )
        self.assertContains(bad_complete, 'Only the retailer can confirm delivery')

        self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'update_status', 'status': Order.OrderStatus.DELIVERED},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.OrderStatus.DELIVERED)
        self.assertFalse(order.retailer_confirmed_delivery)

        # Wholesaler adds a new product -> previous customer (self.retailer) gets notified, non_customer does not
        self.client.post(
            reverse('wholesaler_inventory'),
            {
                'action': 'add_product',
                'store_id': str(self.store.store_id),
                'product_name': 'Organic Jaggery',
                'category': 'Groceries',
                'company_name': 'DesiFarm',
                'capacity': '10kg Box',
                'price': '650.00',
                'available_stock_count': '25',
            },
        )
        self.assertTrue(Notification.objects.filter(recipient=self.retailer, title__icontains='Organic Jaggery').exists())
        self.assertFalse(Notification.objects.filter(recipient=non_customer).exists())

        # Retailer logs in, confirms delivery (-> COMPLETED), and sees notification
        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        notif_page = self.client.get(reverse('notifications_page'))
        self.assertContains(notif_page, 'Organic Jaggery')

        self.client.post(
            reverse('retailer_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'confirm_delivery'},
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.OrderStatus.COMPLETED)
        self.assertTrue(order.retailer_confirmed_delivery)
        self.assertIsNotNone(order.retailer_confirmed_at)

        # Credit-based COMPLETED order with unsettled balance:
        # Retailer still sees "Settle Ledger" and Wholesaler still sees "Manage" (for payments only)
        r_orders = self.client.get(reverse('retailer_orders'))
        self.assertContains(r_orders, '>Settle Ledger<')
        r_detail = self.client.get(reverse('retailer_order_detail', kwargs={'order_id': order.order_id}))
        self.assertContains(r_detail, 'Settle Ledger')

        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'wholesaler1', 'password': 'StrongPassword123!'},
        )
        w_orders = self.client.get(reverse('wholesaler_orders'))
        self.assertContains(w_orders, '>Manage<')
        w_detail = self.client.get(reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}))
        self.assertContains(w_detail, f'Manage Order #{order.short_id}')
        self.assertNotContains(w_detail, 'Update Status')  # status & logistics stay locked
        self.assertContains(w_detail, 'Record Verified Payment')

        # Attempting to overpay beyond remaining balance is rejected
        overpay_resp = self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'record_payment', 'amount': str(order.balance_due + Decimal('500.00')), 'payment_method': 'CASH'},
            follow=True,
        )
        self.assertContains(overpay_resp, 'exceeds remaining balance due')

        # Wholesaler settles full remaining balance on the credit order -> now becomes strictly read-only ("View")
        self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'record_payment', 'amount': str(order.balance_due), 'payment_method': 'CASH'},
        )
        order.refresh_from_db()
        self.assertTrue(order.is_fully_paid)

        w_dash = self.client.get(reverse('wholesaler_dashboard'))
        self.assertContains(w_dash, 'Total Amount Received')
        self.assertContains(w_dash, f'&#8377;{order.grand_total}')
        self.assertContains(w_dash, 'Pending Receivables: <strong>&#8377;0.00</strong>')

        w_orders_settled = self.client.get(reverse('wholesaler_orders'))
        self.assertContains(w_orders_settled, '>View<')
        self.assertNotContains(w_orders_settled, '>Manage<')
        w_detail_settled = self.client.get(reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}))
        self.assertContains(w_detail_settled, f'View Order #{order.short_id}')
        self.assertNotContains(w_detail_settled, 'Record Verified Payment')

        blocked_post = self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {'action': 'record_payment', 'amount': '100.00', 'payment_method': 'CASH'},
            follow=True,
        )
        self.assertContains(blocked_post, 'Completed and cancelled orders are read-only')

        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        r_orders_settled = self.client.get(reverse('retailer_orders'))
        self.assertContains(r_orders_settled, '>View<')
        self.assertNotContains(r_orders_settled, '>Settle Ledger<')
        r_detail_settled = self.client.get(reverse('retailer_order_detail', kwargs={'order_id': order.order_id}))
        self.assertContains(r_detail_settled, f'View #{order.short_id}')
        self.assertNotContains(r_detail_settled, 'Settle Ledger')

    def test_retailer_recommendation_algorithm(self):
        # Add another Groceries item from same wholesaler and an unrelated Beverages item
        jaggery = Stock.objects.create(
            store=self.store,
            sku='JAG-10',
            product_name='Desi Jaggery',
            category='Groceries',
            price=Decimal('700.00'),
            capacity='10kg Box',
            company_name='SweetCo',
            available_stock_count=40,
            min_order_quantity=1,
        )
        tea = Stock.objects.create(
            store=self.other_store,
            sku='TEA-5',
            product_name='Green Tea',
            category='Beverages',
            price=Decimal('900.00'),
            capacity='5kg Pack',
            company_name='LeafCo',
            available_stock_count=40,
            min_order_quantity=1,
        )

        # Retailer1 orders self.stock (Groceries / SweetCo / self.store) twice with qty=5
        api = APIClient()
        api.force_authenticate(user=self.retailer)
        for _ in range(2):
            api.post(
                reverse('orders_api'),
                {'items': [{'product_id': self.stock.id, 'quantity': 5}]},
                format='json',
            )

        rec_resp = api.get(reverse('recommendations_api'))
        self.assertEqual(rec_resp.status_code, status.HTTP_200_OK)
        ids = [entry['product']['id'] for entry in rec_resp.data]
        # Repeat-ordered item ranks #1 with suggested_qty=5, and related Groceries/SweetCo item ranks ahead of unrelated Beverages
        self.assertEqual(ids[0], self.stock.id)
        self.assertEqual(rec_resp.data[0]['suggested_qty'], 5)
        self.assertLess(ids.index(jaggery.id), ids.index(tea.id))

        # Dashboard HTML view renders recommendations cleanly
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        dash = self.client.get(reverse('retailer_dashboard'))
        self.assertContains(dash, 'Recommended for Your Shop')
        self.assertContains(dash, 'Desi Jaggery')

    def test_landing_page_for_new_visitors(self):
        # Root URL redirects to /store/ landing page with login & register CTAs
        root = self.client.get('/')
        self.assertEqual(root.status_code, 302)

        landing = self.client.get(reverse('home'))
        self.assertEqual(landing.status_code, 200)
        self.assertContains(landing, 'Create Free Account')
        self.assertContains(landing, 'Sign In')
        self.assertContains(landing, reverse('login_page'))
        self.assertContains(landing, reverse('register_page'))
        self.assertContains(landing, self.store.store_name)

        # ?role=WHOLESALER pre-selects Wholesaler on the registration page
        reg_w = self.client.get(reverse('register_page') + '?role=WHOLESALER')
        self.assertEqual(reg_w.status_code, 200)
        self.assertContains(reg_w, 'value="WHOLESALER" selected')

    def test_retailer_order_delivery_payment_and_cancellation_flow(self):
        # Retailer logs in via JWT cookies
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )

        # Add 3 bags of Refined Sugar to cart
        add_resp = self.client.post(
            reverse('add_to_cart', kwargs={'product_id': self.stock.id}),
            {'quantity': 3},
        )
        self.assertEqual(add_resp.status_code, 302)

        # Checkout to Pune, Maharashtra (matches ServiceArea fee = 150.00)
        checkout_resp = self.client.post(
            reverse('retailer_cart'),
            {
                'action': 'checkout',
                'delivery_address': '88 MG Road',
                'delivery_city': 'Pune',
                'delivery_state': 'Maharashtra',
                'delivery_pincode': '411001',
                'contact_phone': '9123456789',
            },
        )
        self.assertEqual(checkout_resp.status_code, 302)

        order = Order.objects.get(customer=self.retailer)
        self.stock.refresh_from_db()
        # 20 - 3 = 17 remaining
        self.assertEqual(self.stock.available_stock_count, 17)
        # 2000 * 1.05 * 3 = 6300.00 + 150.00 delivery = 6450.00
        self.assertEqual(order.total_price, Decimal('6300.00'))
        self.assertEqual(order.delivery_fee, Decimal('150.00'))
        self.assertEqual(order.grand_total, Decimal('6450.00'))
        self.assertEqual(order.payment_status, Order.PaymentStatus.UNPAID)

        # Retailer submits partial payment proof (3000.00)
        pay_resp = self.client.post(
            reverse('retailer_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'submit_payment',
                'amount': '3000.00',
                'payment_method': Paymentdetails.PaymentMethod.UPI,
                'reference_number': 'UTR99887766',
            },
        )
        self.assertEqual(pay_resp.status_code, 302)
        payment = Paymentdetails.objects.get(order=order)
        self.assertEqual(payment.status, Paymentdetails.VerificationStatus.PENDING)

        # Wholesaler logs in, verifies partial payment, updates shipment & status
        self.client.get(reverse('logout_page'))
        self.client.post(
            reverse('login_page'),
            {'username': 'wholesaler1', 'password': 'StrongPassword123!'},
        )
        self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'verify_payment',
                'payment_id': str(payment.payment_id),
                'decision': 'approve',
            },
        )
        order.refresh_from_db()
        self.assertEqual(order.amount_paid, Decimal('3000.00'))
        self.assertEqual(order.payment_status, Order.PaymentStatus.PARTIAL)

        self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'update_shipment',
                'tracking_number': 'LR-445566',
                'transport_company': 'VRL Logistics',
                'driver_name': 'Ramesh',
                'driver_phone': '9998887776',
                'vehicle_number': 'GJ01AB1234',
                'current_city': 'Surat',
                'current_state': 'Gujarat',
            },
        )
        self.client.post(
            reverse('wholesaler_order_detail', kwargs={'order_id': order.order_id}),
            {
                'action': 'update_status',
                'status': Order.OrderStatus.IN_TRANSIT,
                'location_city': 'Surat',
                'remarks': 'Crossed Surat hub',
            },
        )
        order.refresh_from_db()
        self.assertEqual(order.status, Order.OrderStatus.IN_TRANSIT)
        self.assertEqual(order.shipment.tracking_number, 'LR-445566')

        # Invoice accessible to wholesaler
        inv_resp = self.client.get(reverse('order_invoice', kwargs={'order_id': order.order_id}))
        self.assertEqual(inv_resp.status_code, 200)
        self.assertContains(inv_resp, 'LR-445566')

        # Cancel test via API restores stock
        api = APIClient()
        api.force_authenticate(user=self.retailer)
        create_resp = api.post(
            reverse('orders_api'),
            {
                'items': [{'product_id': self.stock.id, 'quantity': 2}],
                'delivery_city': 'Pune',
                'delivery_state': 'Maharashtra',
            },
            format='json',
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        new_order_uuid = create_resp.data[0]['order_id']
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available_stock_count, 15)

        cancel_resp = api.post(
            reverse('order_status_api', kwargs={'order_id': new_order_uuid}),
            {'status': Order.OrderStatus.CANCELLED},
            format='json',
        )
        self.assertEqual(cancel_resp.status_code, status.HTTP_200_OK)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.available_stock_count, 17)
        cancelled_order = Order.objects.get(order_id=new_order_uuid)
        self.assertEqual(cancelled_order.balance_due, Decimal('0.00'))

        # Cancelled order shows no outstanding due on retailer views
        self.client.post(
            reverse('login_page'),
            {'username': 'retailer1', 'password': 'StrongPassword123!'},
        )
        r_cancelled_detail = self.client.get(reverse('retailer_order_detail', kwargs={'order_id': new_order_uuid}))
        self.assertContains(r_cancelled_detail, 'Balance Due: <strong>&#8377;0.00</strong>')
        self.assertContains(r_cancelled_detail, 'No Due')
