"""
Shopify REST Admin API client using requests (stdlib-compatible).
"""
import os
import re
import requests

# ─── SSL fix for macOS (Python doesn't use system certs by default) ───────────
try:
    import certifi
    _SSL_VERIFY = certifi.where()
except ImportError:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _SSL_VERIFY = False
# ─────────────────────────────────────────────────────────────────────────────

SHOPIFY_STORE = os.getenv("SHOPIFY_STORE", "rachap-8j.myshopify.com")
SHOPIFY_ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN", "")
API_VERSION = "2024-10"
BASE_URL = f"https://{SHOPIFY_STORE}/admin/api/{API_VERSION}"

HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_ACCESS_TOKEN,
    "Content-Type": "application/json",
}


def _get(endpoint: str, params: dict = None) -> dict:
    resp = requests.get(
        f"{BASE_URL}/{endpoint}.json",
        headers=HEADERS,
        params=params or {},
        timeout=30,
        verify=_SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()


# ─── Orders ──────────────────────────────────────────────────────────────────

def search_orders(query: str, limit: int = 5) -> list:
    params = {"limit": limit, "status": "any"}
    order_num = re.sub(r"[^0-9]", "", query)

    if order_num:
        params["name"] = f"#{order_num}"
    elif "@" in query:
        params["email"] = query

    data = _get("orders", params)
    orders = data.get("orders", [])

    if not orders and "@" in query:
        params.pop("name", None)
        params["email"] = query
        data = _get("orders", params)
        orders = data.get("orders", [])

    return [_simplify_order(o) for o in orders]


def _simplify_order(order: dict) -> dict:
    fulfillments = order.get("fulfillments", [])
    tracking = []
    delivered_at = None
    latest_shipment_status = None
    for f in fulfillments:
        shipment_status = f.get("shipment_status")
        updated = f.get("updated_at")
        if shipment_status == "delivered" and updated:
            if delivered_at is None or updated > delivered_at:
                delivered_at = updated
        if f.get("status") == "success" and not delivered_at and updated:
            if latest_shipment_status is None or updated > (latest_shipment_status or ""):
                latest_shipment_status = updated
        tracking.append({"status": f.get("status"), "shipment_status": shipment_status, "tracking_number": f.get("tracking_number"), "tracking_url": f.get("tracking_url"), "tracking_company": f.get("tracking_company"), "updated_at": updated})
    items = [{"title": i.get("title"), "variant_title": i.get("variant_title"), "quantity": i.get("quantity"), "price": i.get("price")} for i in order.get("line_items", [])]
    shipping = order.get("shipping_address") or {}
    return {"id": order.get("id"), "order_number": order.get("name"), "email": order.get("email"), "created_at": order.get("created_at"), "financial_status": order.get("financial_status"), "fulfillment_status": order.get("fulfillment_status") or "unfulfilled", "total_price": order.get("total_price"), "currency": order.get("currency"), "items": items, "tracking": tracking, "delivered_at": delivered_at or latest_shipment_status, "shipping_address": {"name": shipping.get("name"), "address": f"{shipping.get('address1', '')} {shipping.get('address2', '')}".strip(), "city": shipping.get("city"), "province": shipping.get("province"), "country": shipping.get("country"), "zip": shipping.get("zip")}, "note": order.get("note")}


def search_products(query, limit=5):
    data = _get("products", {"limit": limit, "title": query})
    products = data.get("products", [])
    if not products:
        all_data = _get("products", {"limit": 50})
        q_lower = query.lower()
        products = [p for p in all_data.get("products", []) if q_lower in p.get("title", "").lower()][:limit]
    return products


def get_policies():
    data = _get("policies")
    return [{"title": p.get("title"), "body": p.get("body"), "url": p.get("url")} for p in data.get("policies", [])]


def search_customers(email):
    data = _get("customers/search", {"query": f"email:{email}"})
    return [{"id": c.get("id"), "email": c.get("email"), "first_name": c.get("first_name"), "last_name": c.get("last_name"), "orders_count": c.get("orders_count"), "total_spent": c.get("total_spent"), "created_at": c.get("created_at"), "tags": c.get("tags")} for c in data.get("customers", [])]
