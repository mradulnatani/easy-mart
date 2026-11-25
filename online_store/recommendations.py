from collections import defaultdict

from django.db.models import Count, F
from django.utils import timezone

from .models import Order, OrderItem, Stock


def get_retailer_recommendations(retailer, limit: int = 6) -> list[dict]:
    """
    Hybrid B2B recommendation engine for a retailer based on their previous orders:
    1. Replenishment / repeat-order affinity (frequency + recency + qty)
    2. Content-based category, brand, and preferred-wholesaler affinity
    3. Item-based collaborative filtering (co-purchased by similar retailers)
    4. Cold-start fallback (local serviceability + global popularity)
    """
    in_stock = list(
        Stock.objects.filter(available_stock_count__gte=F('min_order_quantity'))
        .select_related('store')
        .prefetch_related('store__service_areas')
        .order_by('-updated_at')[:500]
    )
    if not in_stock:
        return []

    now = timezone.now()

    past_items = list(
        OrderItem.objects.filter(order__customer=retailer)
        .exclude(order__status=Order.OrderStatus.CANCELLED)
        .select_related('product', 'order', 'order__store')
        .order_by('-order__created_at')[:200]
    )

    ordered_counts: dict[int, int] = defaultdict(int)
    last_ordered_at: dict[int, timezone.datetime] = {}
    last_qty: dict[int, int] = {}
    category_weights: dict[str, float] = defaultdict(float)
    brand_weights: dict[str, float] = defaultdict(float)
    store_weights: dict[int, float] = defaultdict(float)

    for oi in past_items:
        age_days = max(0.0, (now - oi.order.created_at).total_seconds() / 86400.0)
        recency_weight = 1.0 / (1.0 + age_days / 30.0)
        ordered_counts[oi.product_id] += 1
        if oi.product_id not in last_ordered_at:
            last_ordered_at[oi.product_id] = oi.order.created_at
            last_qty[oi.product_id] = oi.quantity
        category_weights[oi.product.category.lower()] += recency_weight
        brand_weights[oi.product.company_name.lower()] += recency_weight
        store_weights[oi.order.store_id] += recency_weight

    # Collaborative filtering: products bought by other retailers who bought the same items
    collab_scores: dict[int, float] = defaultdict(float)
    if ordered_counts:
        peer_customer_ids = list(
            OrderItem.objects.filter(product_id__in=ordered_counts.keys())
            .exclude(order__customer=retailer)
            .exclude(order__status=Order.OrderStatus.CANCELLED)
            .values_list('order__customer_id', flat=True)
            .distinct()[:50]
        )
        if peer_customer_ids:
            peer_counts = (
                OrderItem.objects.filter(order__customer_id__in=peer_customer_ids)
                .exclude(order__status=Order.OrderStatus.CANCELLED)
                .exclude(product_id__in=ordered_counts.keys())
                .values('product_id')
                .annotate(cnt=Count('id'))
                .order_by('-cnt')[:200]
            )
            for row in peer_counts:
                collab_scores[row['product_id']] = float(row['cnt'])

    # Global popularity fallback signal computed in SQL
    popularity_counts: dict[int, int] = {
        row['product_id']: row['cnt']
        for row in (
            OrderItem.objects.exclude(order__status=Order.OrderStatus.CANCELLED)
            .values('product_id')
            .annotate(cnt=Count('id'))
            .order_by('-cnt')[:200]
        )
    }

    retailer_city = (getattr(retailer, 'city', '') or '').strip().lower()
    retailer_state = (getattr(retailer, 'state', '') or '').strip().lower()

    scored: list[dict] = []
    for product in in_stock:
        score = 0.0
        reasons: list[tuple[float, str]] = []

        # 1. Repeat / Replenishment score
        freq = ordered_counts.get(product.id, 0)
        if freq > 0:
            days_since = max(0.0, (now - last_ordered_at[product.id]).total_seconds() / 86400.0)
            rep_score = (freq * 5.0) + (2.0 if days_since >= 7 else 0.5)
            score += rep_score
            reasons.append((rep_score, f'Ordered {freq}x previously (quick restock)'))

        # 2. Content-based category, brand & store affinity
        cat_w = category_weights.get(product.category.lower(), 0.0)
        brand_w = brand_weights.get(product.company_name.lower(), 0.0)
        store_w = store_weights.get(product.store_id, 0.0)
        content_score = (cat_w * 3.5) + (brand_w * 2.5) + (store_w * 1.5)
        if content_score > 0:
            score += content_score
            if freq == 0:
                if cat_w >= brand_w:
                    reasons.append((content_score, f'Matches your {product.category} purchases'))
                else:
                    reasons.append((content_score, f'From {product.company_name} / {product.store.store_name}'))

        # 3. Collaborative filtering
        cf_score = collab_scores.get(product.id, 0.0) * 2.2
        if cf_score > 0:
            score += cf_score
            reasons.append((cf_score, 'Popular with similar retailers'))

        # 4. Local delivery coverage bonus
        serves_local = False
        if retailer_city or retailer_state:
            if (retailer_city and product.store.city.lower() == retailer_city) or (
                retailer_state and product.store.state.lower() == retailer_state
            ):
                serves_local = True
            else:
                for area in product.store.service_areas.all():
                    if area.is_active and (
                        (retailer_city and area.city.lower() == retailer_city)
                        or (retailer_state and area.state.lower() == retailer_state)
                    ):
                        serves_local = True
                        break
        if serves_local:
            score += 1.2
            reasons.append((1.2, f'Delivers to {retailer.city or retailer.state}'))

        # 5. Global popularity
        pop_score = min(popularity_counts.get(product.id, 0) * 0.4, 3.0)
        if pop_score > 0:
            score += pop_score
            reasons.append((pop_score, 'Trending wholesale item'))

        if not reasons:
            score += 0.1
            reasons.append((0.1, f'Available at {product.store.store_name}'))

        reasons.sort(key=lambda item: item[0], reverse=True)
        suggested_qty = last_qty.get(product.id, product.min_order_quantity)
        suggested_qty = max(product.min_order_quantity, min(suggested_qty, product.available_stock_count))

        scored.append({
            'product': product,
            'score': round(score, 2),
            'reason': reasons[0][1],
            'suggested_qty': suggested_qty,
        })

    scored.sort(key=lambda item: (-item['score'], -item['product'].available_stock_count, item['product'].id))
    return scored[:limit]
