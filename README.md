# Easy-Mart — B2B Wholesale & Retailer Platform

Easy-Mart is a mobile-first Django web application and REST API built for **Wholesalers** and **Retailers** operating across multiple cities and states. It replaces manual phone ordering with self-serve wholesale ordering, automated stock count maintenance, multi-city delivery tracking, payment & credit-ledger reconciliation, scannable shop QR codes, new-product alerts, and order-history recommendations.

---

## Key Features

### Authentication & Role Isolation
- **Two User Roles**: `WHOLESALER` and `RETAILER` ([`CustomUser`](online_store/models.py)).
- **JWT Auth for SSR HTML & API**: Uses `djangorestframework-simplejwt` with `HttpOnly` cookies (`JWTCookieMiddleware` & `JWTCookieAuthentication` in [`online_store/auth.py`](online_store/auth.py)) alongside standard `Authorization: Bearer <token>` headers for REST clients, with refresh-token rotation and blacklisting on logout.
- **Automatic Shop Setup**: Registering as a wholesaler automatically creates their initial [`StoreDetails`](online_store/models.py) storefront from their business profile.

### Wholesaler Portal
- **Stores, Multi-City Coverage & Shop QR Codes**:
  - Configure serviceable cities/states with transit times and delivery fees ([`ServiceArea`](online_store/models.py)).
  - View, print, and download each shop's **QR code** in **PNG** or **SVG** format (generated in pure Python by [`online_store/qr.py`](online_store/qr.py)).
  - Scanning the QR code redirects any retailer or customer straight to `/store/shop/<store_id>/` to browse that wholesaler's live products.
- **Stock Count Maintenance & Audit Trail**:
  - Add products manually or bulk upload/update via **Excel (`.xlsx`)** or **CSV (`.csv`)** ([`import_stock_spreadsheet`](online_store/services.py))—no target-store selection required; flexible column aliases, currency symbols, and encodings (`utf-8`, `cp1252`, `latin-1`) are handled automatically.
  - Export current inventory to CSV, monitor low-stock alerts, and track every stock change (restock, order deduction, cancellation restore, damaged goods, discrepancy correction) in [`ProductAudit`](online_store/models.py).
- **Order & Logistics Management**:
  - Update order statuses (`PENDING` → `CONFIRMED` → `PACKED` → `SHIPPED` → `IN_TRANSIT` → `OUT_FOR_DELIVERY` → `DELIVERED`), attach LR/tracking numbers, transport company, driver name/phone, vehicle number, current city/state, and ETA ([`Shipment`](online_store/models.py)).
  - Completed and cancelled orders are strictly read-only (`View` only).
- **Payment Ledger & Verification**:
  - Track retailer-wise total billed, verified receipts, and balance due, and approve/reject UPI payment proofs or record Cash/Cheque/Bank payments.

### Retailer Portal
- **Browse All Wholesalers & Catalog**:
  - View every registered wholesale shop on the dashboard and `/store/retailer/shops/`, or filter products by city, state, wholesaler, category, or SKU.
- **Cart, Multi-Wholesaler Checkout & Reordering**:
  - Add items respecting minimum order quantities (MOQ) and available stock; checkout splits multi-wholesaler carts into separate trackable orders with atomic stock locking (`select_for_update`).
  - One-click reorder from past orders and printable tax invoices / delivery challans.
- **₹10,000 Credit Limit & UPI-Only Proof**:
  - Every retailer has a default **₹10,000.00 credit limit** to defer payment at checkout or on unpaid orders without being prompted for a payment type or proof during credit ordering.
  - When submitting a payment, the proof/screenshot upload field is shown and accepted **only when `UPI` is selected**.
- **Two-Step Delivery Confirmation**:
  - Wholesalers mark orders `DELIVERED`, and retailers confirm receipt (`Confirm Delivery Received`) to transition the order to `COMPLETED` (read-only).
- **New-Product Alerts & Recommendations**:
  - Retailers receive in-app (and email) [`Notification`](online_store/models.py) alerts whenever a wholesaler they previously ordered from adds new products.
  - Hybrid order-history recommendation engine ([`online_store/recommendations.py`](online_store/recommendations.py)) scores products by repeat-order frequency/recency, category/brand/store affinity, collaborative filtering, and local delivery coverage.

### Mobile-First Responsive UI
- Built with Django HTML templates ([`templates/`](templates/)) and responsive CSS ([`static/css/app.css`](static/css/app.css)), featuring a collapsible top menu, fixed mobile bottom navigation bar, 2×2 stat cards, `16px` inputs (preventing mobile auto-zoom), and 44px touch targets.

---

## Tech Stack

- **Backend**: Python 3.12, Django 6.1.2, Django REST Framework 3.18.3
- **Auth**: `djangorestframework-simplejwt` 5.5.1 (`PyJWT` 2.15.1)
- **Frontend**: Django HTML Templates + vanilla CSS (no external JS/CSS build step required)
- **Database**: SQLite (`db.sqlite3`) by default

---

## Project Structure

```text
easy-mart/
├── easy_mart/                  # Django project settings & root URLs
├── online_store/
│   ├── api_views.py            # DRF REST API endpoints
│   ├── auth.py                 # JWT cookie middleware, auth class & role decorators
│   ├── context_processors.py   # Cart count & unread notification count
│   ├── models.py               # Users, Stores, ServiceAreas, Stock, Orders, Shipments, Payments, Audits, Notifications
│   ├── permissions.py          # IsWholesaler, IsRetailer, IsStoreOwner
│   ├── qr.py                   # Pure-Python QR code generator (SVG & PNG)
│   ├── recommendations.py      # Retailer order-history recommendation engine
│   ├── serializer.py           # DRF serializers
│   ├── services.py             # Transactional business logic & .xlsx/.csv importer
│   ├── tests.py                # End-to-end test suite
│   ├── urls.py                 # Web & API routes under /store/
│   └── web_views.py            # Server-rendered HTML views
├── static/css/app.css          # Mobile-first stylesheet
├── templates/                  # Django HTML templates (auth, wholesaler, retailer, shop, orders, notifications)
├── manage.py
└── requirements.txt
```

---

## Local Setup & Running

1. **Create and activate a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Apply database migrations**
   ```bash
   python manage.py migrate
   ```

4. **Create a superuser (optional, for `/admin/`)**
   ```bash
   python manage.py createsuperuser
   ```

5. **Run the development server**
   ```bash
   python manage.py runserver
   ```
   Open `http://127.0.0.1:8000/` in your browser (or via your local network IP on a mobile phone with `python manage.py runserver 0.0.0.0:8000`).

---

## Bulk Stock Upload Format (`.xlsx` or `.csv`)

Wholesalers can upload `.xlsx` Excel sheets or `.csv` files directly from **Inventory → Bulk Upload** (or `POST /store/api/uploadviacsv/`).

| Column | Required | Notes / Accepted Aliases |
| --- | --- | --- |
| `product_name` | Yes | `product`, `item_name`, `item`, `name` |
| `category` | No | Defaults to `General` (`cat`) |
| `price` | No | Supports symbols/commas like `₹1,450.00` (`unit_price`, `rate`, `mrp`) |
| `capacity` | No | Defaults to `Standard` (`pack_size`, `size`, `weight`, `volume`) |
| `company_name` | No | Defaults to `Generic` (`company`, `brand`, `manufacturer`) |
| `available_stock_count` | No | Accepts integer or decimal like `30.0` (`stock`, `quantity`, `qty`, `count`) |
| `sku` | No | Auto-generated if blank; existing `(store, sku)` rows are updated (`item_code`, `code`) |
| `unit_type` | No | `BOX`, `CARTON`, `KG`, `LITER`, `PIECE`, `PACK` (`unit`) |
| `min_order_quantity` | No | Defaults to `1` (`moq`) |
| `low_stock_threshold` | No | Defaults to `10` (`threshold`) |
| `gst_rate` | No | Defaults to `0.00` (`gst`, `tax`, `tax_rate`) |

---

## Key Routes

### Web Views (`/store/`)
- **Auth & Shared**: `/store/`, `/store/login/`, `/store/register/`, `/store/logout/`, `/store/profile/`, `/store/notifications/`
- **QR Storefront**: `/store/shop/<store_id>/`
- **Wholesaler Portal**: `/store/wholesaler/`, `/store/wholesaler/stores/`, `/store/wholesaler/stores/<store_id>/qr/`, `/store/wholesaler/stores/<store_id>/qr/download/?format=png|svg`, `/store/wholesaler/inventory/`, `/store/wholesaler/inventory/export/`, `/store/wholesaler/inventory/audit/`, `/store/wholesaler/orders/`, `/store/wholesaler/orders/<order_id>/`, `/store/wholesaler/payments/`
- **Retailer Portal**: `/store/retailer/`, `/store/retailer/shops/`, `/store/retailer/catalog/`, `/store/retailer/cart/`, `/store/retailer/orders/`, `/store/retailer/orders/<order_id>/`, `/store/retailer/payments/`
- **Invoice**: `/store/orders/<order_id>/invoice/`

### REST API (`/store/api/` & `/api/token/`)
- **JWT Tokens**: `POST /api/token/`, `POST /api/token/refresh/`, `POST /store/api/register/`, `POST /store/api/login/`, `POST /store/api/logout/`, `GET/PATCH /store/api/profile/`
- **Stores & QR**: `GET/POST /store/api/stores/`, `GET/POST /store/api/stores/<store_id>/service-areas/`, `GET /store/api/stores/<store_id>/qr/download/`
- **Inventory & Audit**: `GET /store/api/products/`, `POST /store/api/uploadviacsv/`, `GET/PUT/PATCH /store/api/editproduct/<pk>/`, `GET /store/api/inventory/audit/`
- **Orders & Shipments**: `GET/POST /store/api/orders/`, `GET /store/api/orders/<order_id>/`, `POST /store/api/orders/<order_id>/status/`, `POST /store/api/orders/<order_id>/confirm-delivery/`, `POST /store/api/orders/<order_id>/use-credit/`, `PATCH /store/api/orders/<order_id>/shipment/`
- **Payments, Alerts & Recommendations**: `GET/POST /store/api/payments/`, `POST /store/api/payments/<payment_id>/verify/`, `GET/POST /store/api/notifications/`, `GET /store/api/recommendations/`

---

## Running Tests

```bash
python manage.py check
python manage.py test online_store -v 2
```
