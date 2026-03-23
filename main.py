"""
Monteiro Jewels — Support Agent Dashboard
Backend using Python's built-in http.server (zero external dependencies for core).
"""
import os
import re
import json
import ssl
import smtplib
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── SSL fix for macOS (Python.org installer doesn't use system cert store) ───
# Patch REQUESTS_CA_BUNDLE and SSL_CERT_FILE so all HTTPS calls use certifi.
try:
    import certifi as _certifi
    _ca = _certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", _ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)
except ImportError:
    pass  # certifi not installed; run.command will have set the env vars

# Load .env manually
def load_env(path=".env"):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip())

load_env()

import database
import shopify_client


# ─── SMTP Email Sending (Gmail App Password) ─────────────────────────────────

def smtp_ready() -> bool:
    user = os.getenv("GMAIL_USER", "")
    pwd  = os.getenv("GMAIL_APP_PASSWORD", "")
    return bool(user and pwd and "your_" not in pwd and len(pwd.replace(" ", "")) >= 16)


def send_email_smtp(to: str, subject: str, body: str, thread_id: str = None) -> dict:
    """Send an email via Gmail SMTP using App Password. Returns {ok, error}."""
    user = os.getenv("GMAIL_USER", "")
    pwd  = os.getenv("GMAIL_APP_PASSWORD", "")

    if not smtp_ready():
        return {"ok": False, "error": "SMTP not configured. Add GMAIL_USER and GMAIL_APP_PASSWORD to .env"}

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"Monteiro Jewels <{user}>"
    msg["To"]      = to
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl._create_unverified_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(user, pwd)
            server.sendmail(user, to, msg.as_string())
        return {"ok": True}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "error": "Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# Try to import Gmail client (needs google packages)
try:
    import gmail_client
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False
    print("  ⚠  Gmail client unavailable (install google-api-python-client)")


# ─── Agent Configuration & Policy ───────────────────────────────────────────
#
# This section defines the agent's behaviour, tone, return rules, and the
# policy URLs the draft generator uses to construct accurate responses.
# Edit this block to update store policies without touching any other code.

AGENT_CONFIG = {

    # ── Brand voice ──────────────────────────────────────────────────────────
    "brand_name": "Monteiro Jewels",
    "tone": (
        "Always be warm, polite, and professional. "
        "Address the customer by their first name. "
        "Acknowledge the inconvenience before explaining the procedure. "
        "Never make promises beyond what the policy allows. "
        "Keep responses concise but complete."
    ),

    # ── Return / exchange policy ──────────────────────────────────────────────
    "return_policy": {
        "window_days": 14,          # calendar days after confirmed delivery
        "requires_photos": True,    # always request photo evidence
        "confirm_email": True,      # always confirm customer's registered email
        "return_label": True,       # store provides the return shipping label
        "refund_processing_days": 5,# business days after item received back
        "condition": (
            "Items must be unworn, in original packaging, with all tags attached."
        ),
        "url": "https://monteirojewels.com/policies/refund-policy",
    },

    # ── Shipping policy ───────────────────────────────────────────────────────
    "shipping_policy": {
        "url": "https://monteirojewels.com/policies/shipping-policy",
        "standard_days": "5–10 business days",
        "express_days":  "2–3 business days",
    },

    # ── Privacy & Terms ───────────────────────────────────────────────────────
    "privacy_policy_url": "https://monteirojewels.com/policies/privacy-policy",
    "terms_url":          "https://monteirojewels.com/policies/terms-of-service",

    # ── Contact ───────────────────────────────────────────────────────────────
    "support_email": "monteirojewels@gmail.com",
    "store_url":     "https://monteirojewels.com",
}


# ─── Email Classification & Draft Generation ────────────────────────────────

def classify_email(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    scores = {
        "CANCELAMENTO": sum(1 for w in ["cancel", "cancelar", "cancelamento"] if w in text),
        "TROCA_DEVOLUCAO": sum(1 for w in ["return", "devol", "troca", "exchange", "refund", "reembolso"] if w in text),
        "STATUS_PEDIDO": sum(1 for w in ["status", "rastreio", "tracking", "entrega", "delivery", "onde está", "where is", "enviado", "shipped"] if w in text),
        "DUVIDA_PRODUTO": sum(1 for w in ["disponível", "available", "preço", "price", "estoque", "tamanho", "dúvida", "question"] if w in text),
        "PROBLEMA_PEDIDO": sum(1 for w in ["problema", "problem", "defeito", "errado", "wrong", "danificado", "damaged", "faltando", "missing"] if w in text),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "OUTRO"


def detect_language(text: str) -> str:
    text_lower = text.lower()
    pt = sum(1 for w in ["olá", "obrigado", "pedido", "gostaria", "por favor", "entrega", "produto"] if w in text_lower)
    en = sum(1 for w in ["hello", "thank", "order", "would like", "please", "delivery", "product"] if w in text_lower)
    es = sum(1 for w in ["hola", "gracias", "pedido", "quisiera", "por favor", "producto"] if w in text_lower)
    if en > pt and en > es:
        return "en"
    if es > pt and es > en:
        return "es"
    return "pt-BR"


def extract_name(from_str: str) -> str:
    match = re.match(r'^(.+?)\s*<', from_str)
    return match.group(1).strip().strip('"') if match else from_str.split("@")[0]


def extract_email(from_str: str) -> str:
    match = re.search(r'<(.+?)>', from_str)
    return match.group(1) if match else from_str


def _days_since(date_str: str) -> int | None:
    """Return calendar days elapsed since an ISO-8601 date string, or None if unparseable."""
    if not date_str:
        return None
    try:
        # Strip timezone offset so fromisoformat works on Python < 3.11
        clean = re.sub(r"[+-]\d{2}:\d{2}$", "", date_str.replace("Z", ""))
        dt = datetime.fromisoformat(clean).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).days
    except Exception:
        return None


def _extract_order_number(text: str) -> str | None:
    """Return the first order number found in text, or None.

    Patterns recognised (case-insensitive):
      order #2308 | pedido #2308 | order 2308 | pedido nº 2308 | #2308 | nº 2308
    Falls back to any standalone 3–6 digit number.
    """
    # Explicit keyword patterns first
    m = re.search(r"(?:order|pedido|nº|numero|number)\s*[#nº]?\s*(\d{3,6})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Hash prefix
    m = re.search(r"#\s*(\d{3,6})\b", text)
    if m:
        return m.group(1)
    # Bare number fallback (only if the text contains an order-related keyword nearby)
    if re.search(r"(?:order|pedido|compra|purchase|invoice|fatura)", text, re.IGNORECASE):
        m = re.search(r"\b(\d{3,6})\b", text)
        if m:
            return m.group(1)
    return None


# Jewelry product keywords — used to extract product mentions from any email
_PRODUCT_KEYWORDS = [
    "watch", "relógio", "relogio",
    "bracelet", "pulseira",
    "necklace", "colar",
    "ring", "anel",
    "earring", "brinco",
    "pendant", "pingente",
    "chain", "corrente",
    "brooch", "broche",
    "choker",
    "anklet", "tornozeleira",
    "set", "conjunto",
    "jewel", "joia", "jóia",
]


def _extract_product_hints(subject: str, body: str) -> list[str]:
    """Extract product search hints from the email text.

    Returns a prioritised list of candidate search strings:
      1. Any sentence / phrase that contains a jewelry keyword
      2. The subject line itself if it contains a keyword
      3. All individual jewelry keywords found (as last resort)
    """
    full_text = f"{subject}\n{body}"
    hints = []
    seen = set()

    # Pass 1: extract short noun-phrase windows (up to 6 words) around keywords
    for kw in _PRODUCT_KEYWORDS:
        # Find keyword in text
        for m in re.finditer(re.escape(kw), full_text, re.IGNORECASE):
            start = m.start()
            # Take up to 40 chars either side and strip to word boundaries
            snippet = full_text[max(0, start - 30): start + len(kw) + 30]
            snippet = re.sub(r"[^a-zA-ZÀ-ÿ0-9\s\-]", " ", snippet).strip()
            snippet = " ".join(snippet.split()[:6])   # max 6 words
            if snippet and snippet.lower() not in seen:
                seen.add(snippet.lower())
                hints.append(snippet)

    # Pass 2: if subject contains a keyword, add the whole subject as a search
    if any(kw in subject.lower() for kw in _PRODUCT_KEYWORDS):
        clean_sub = re.sub(r"[^a-zA-ZÀ-ÿ0-9\s]", " ", subject).strip()
        if clean_sub.lower() not in seen:
            hints.insert(0, clean_sub)

    return hints


def _compute_return_window(order: dict, window: int) -> dict:
    """Given a Shopify order dict, return return-window metadata."""
    delivered_at = order.get("delivered_at")
    days = _days_since(delivered_at)
    if days is None:
        status = "not_delivered"
    elif days <= window:
        status = "eligible"
    else:
        status = "expired"
    return {
        "return_window":       status,
        "return_days_elapsed": days,
        "return_policy_days":  window,
        "delivered_at":        delivered_at,
    }


def query_shopify(ticket: dict) -> dict:  # noqa: C901
    """Query Shopify for ALL relevant data before generating a draft.

    Scanning order (runs for every ticket regardless of category):
      1. Extract order number from SUBJECT first, then BODY
      2. Look for product name mentions in subject + body (jewelry keywords)
      3. Fetch order by number → also fetch the matching Shopify products for
         each line item so the draft can reference exact product names/details
      4. Fallback: fetch recent orders by customer email if no order number found
      5. Resolve product mentions against the Shopify product catalogue
      6. Fetch store policies for return/cancellation/problem categories
      7. Fetch customer history for any ticket where we have an email

    Extra fields added to `data`:
      extracted_order_number  – raw number parsed from email text
      mentioned_products      – list of Shopify products matched from email text
      order_item_products     – list of Shopify products matching the order's line items
      return_window           – 'eligible' | 'expired' | 'not_delivered'
      return_days_elapsed     – int days since delivery
      delivered_at            – ISO date string or None
    """
    data: dict = {}
    subject  = ticket.get("subject", "")
    body     = ticket.get("body", "")
    category = ticket.get("category", "")
    email    = ticket.get("customer_email", "")
    window   = AGENT_CONFIG["return_policy"]["window_days"]
    full_text = f"{subject}\n{body}"

    # ════════════════════════════════════════════════════════════════════════
    # STEP 1 — Extract order number (subject takes priority)
    # ════════════════════════════════════════════════════════════════════════
    order_num = _extract_order_number(subject) or _extract_order_number(body)
    data["extracted_order_number"] = order_num

    # ════════════════════════════════════════════════════════════════════════
    # STEP 2 — Extract product hints from the email text (ALL categories)
    # ════════════════════════════════════════════════════════════════════════
    product_hints = _extract_product_hints(subject, body)
    data["product_hints"] = product_hints  # for debugging / transparency

    # ════════════════════════════════════════════════════════════════════════
    # STEP 3 — Fetch order by number
    # ════════════════════════════════════════════════════════════════════════
    if order_num:
        try:
            orders = shopify_client.search_orders(order_num)
            if orders:
                order = orders[0]
                data["order"] = order
                data["order_number"] = order.get("order_number")

                # Compute return window for every ticket that has an order
                rw = _compute_return_window(order, window)
                data.update(rw)
        except Exception as exc:
            data["order_error"] = str(exc)

    # ════════════════════════════════════════════════════════════════════════
    # STEP 4 — Customer lookup + email-based order fallback
    # ════════════════════════════════════════════════════════════════════════
    if email:
        try:
            customers = shopify_client.search_customers(email)
            if customers:
                data["customer"] = customers[0]
        except Exception:
            pass

        if "order" not in data:
            try:
                orders_by_email = shopify_client.search_orders(email)
                if orders_by_email:
                    data["recent_orders"] = orders_by_email[:3]
                    # Use most recent order as the primary order
                    order = orders_by_email[0]
                    data["order"] = order
                    data["order_number"] = order.get("order_number")
                    rw = _compute_return_window(order, window)
                    data.update(rw)
            except Exception:
                pass

    # ════════════════════════════════════════════════════════════════════════
    # STEP 5 — Resolve product mentions against Shopify catalogue (ALL cats)
    #
    # Two sub-steps:
    #   5a. Look up products explicitly named / described in the email text
    #   5b. Look up the actual products from the order's line items
    # ════════════════════════════════════════════════════════════════════════

    # 5a. Scan email text for product references
    mentioned_products = []
    seen_product_ids: set = set()
    for hint in product_hints:
        if len(hint) < 3:
            continue
        try:
            results = shopify_client.search_products(hint)
            for p in results:
                if p.get("id") not in seen_product_ids:
                    seen_product_ids.add(p.get("id"))
                    mentioned_products.append(p)
            if mentioned_products:
                break   # stop after first successful match
        except Exception:
            pass
    data["mentioned_products"] = mentioned_products

    # For legacy compatibility: set data["product"] to first mentioned product
    if mentioned_products:
        data["product"] = mentioned_products[0]

    # 5b. Cross-reference order line items with Shopify product catalogue
    order_item_products = []
    order = data.get("order")
    if order and order.get("items"):
        for item in order["items"]:
            title = item.get("title", "")
            if not title or len(title) < 3:
                continue
            try:
                results = shopify_client.search_products(title)
                for p in results:
                    if p.get("id") not in seen_product_ids:
                        seen_product_ids.add(p.get("id"))
                        order_item_products.append(p)
                if order_item_products:
                    break
            except Exception:
                pass
    data["order_item_products"] = order_item_products

    # If we found order item products but no mentioned products, use them
    if not data.get("product") and order_item_products:
        data["product"] = order_item_products[0]

    # ════════════════════════════════════════════════════════════════════════
    # STEP 6 — Store policies
    # ════════════════════════════════════════════════════════════════════════
    if category in ("TROCA_DEVOLUCAO", "CANCELAMENTO", "PROBLEMA_PEDIDO"):
        try:
            data["policies"] = shopify_client.get_policies()
        except Exception:
            pass

    return data


def generate_draft(ticket: dict) -> str:  # noqa: C901 – complexity OK for this dispatcher
    """Generate an intelligent draft response based on ticket data and AGENT_CONFIG policy.

    Decision tree:
      STATUS_PEDIDO    → order status + tracking details
      TROCA_DEVOLUCAO  → check 14-day delivery window:
                           eligible    → acknowledge, request photos + email confirm
                           expired     → politely deny, cite policy
                           not_delivered → ask for patience, offer tracking
                           unknown     → ask for order number
      CANCELAMENTO     → check shipment status; offer cancel or redirect to return
      PROBLEMA_PEDIDO  → acknowledge, request photos + order details
      DUVIDA_PRODUTO   → product details from Shopify
      OUTRO            → generic acknowledgement
    """
    cfg      = AGENT_CONFIG
    brand    = cfg["brand_name"]
    rp       = cfg["return_policy"]
    sp       = cfg["shipping_policy"]
    name     = ticket.get("customer_name", "Cliente")
    lang     = ticket.get("language", "pt-BR")
    data     = ticket.get("shopify_data") or {}
    category = ticket.get("category", "OUTRO")
    customer_email = ticket.get("customer_email", "")
    is_en = lang == "en"
    is_es = lang == "es"

    # ── i18n helpers ──────────────────────────────────────────────────────────
    def t(en: str, pt: str, es: str = "") -> str:
        if is_en: return en
        if is_es: return es or pt
        return pt

    greeting  = t(f"Hi {name}", f"Olá {name}", f"Hola {name}")
    thanks    = t("Thank you for contacting us!", "Obrigado por entrar em contato!", "¡Gracias por contactarnos!")
    closing   = t(f"Best regards,\n{brand}", f"Atenciosamente,\n{brand}", f"Atentamente,\n{brand}")
    help_line = t("If you have any further questions, feel free to reach out.",
                  "Qualquer dúvida adicional, estou à disposição.",
                  "Si tiene más preguntas, no dude en escribirnos.")

    order              = data.get("order")
    product            = data.get("product")           # first matched product (text OR order item)
    mentioned_products = data.get("mentioned_products", [])   # products named in email
    order_item_products = data.get("order_item_products", []) # products from order line items
    return_window = data.get("return_window")       # eligible | expired | not_delivered | None
    days_elapsed  = data.get("return_days_elapsed")
    delivered_at  = data.get("delivered_at")
    extracted_num = data.get("extracted_order_number")

    # Build a rich product context string to embed in replies when relevant
    def _product_context() -> str:
        """Return a compact product detail line if we have Shopify product data."""
        p = product
        if not p:
            return ""
        parts = []
        if p.get("title"):
            parts.append(p["title"])
        if p.get("variants"):
            price = p["variants"][0].get("price")
            if price:
                parts.append(t(f"(price: {price})", f"(preço: R$ {price})"))
        if p.get("url"):
            parts.append(t(f"— {p['url']}", f"— {p['url']}"))
        return " ".join(parts)

    lines = [f"{greeting},\n", f"{thanks}\n"]

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: ORDER STATUS
    # ══════════════════════════════════════════════════════════════════════════
    if category == "STATUS_PEDIDO":
        if order:
            on      = order.get("order_number", "")
            status  = order.get("fulfillment_status", "unfulfilled")
            tracking = order.get("tracking", [])
            items_str = ", ".join(i["title"] for i in order.get("items", []))

            lines.append(t(f"I located your order {on}.", f"Localizei seu pedido {on}.") + "\n")

            # Human-friendly status labels
            status_label = {
                "fulfilled":   t("Shipped ✓",           "Enviado ✓"),
                "unfulfilled": t("Processing / not shipped yet", "Em processamento — ainda não enviado"),
                "partial":     t("Partially shipped",   "Enviado parcialmente"),
                "restocked":   t("Returned to stock",   "Devolvido ao estoque"),
            }.get(status, status)
            lines.append(t(f"Status: {status_label}", f"Status: {status_label}"))

            if items_str:
                lines.append(t(f"Items: {items_str}", f"Itens: {items_str}"))

            if tracking:
                tr = tracking[0]
                if tr.get("tracking_number"):
                    lines.append("")
                    lines.append(t(f"Tracking number: {tr['tracking_number']}",
                                   f"Código de rastreio: {tr['tracking_number']}"))
                    if tr.get("tracking_url"):
                        lines.append(t(f"Track your package: {tr['tracking_url']}",
                                       f"Acompanhe sua entrega: {tr['tracking_url']}"))
                    if tr.get("tracking_company"):
                        lines.append(t(f"Carrier: {tr['tracking_company']}",
                                       f"Transportadora: {tr['tracking_company']}"))

            if order.get("delivered_at"):
                lines.append("")
                lines.append(t("Your order has been delivered.", "Seu pedido foi entregue."))

            # Show enriched product detail if we resolved it from the order items
            pc = _product_context()
            if pc and order_item_products:
                lines.append("")
                lines.append(t(f"Product detail: {pc}", f"Detalhes do produto: {pc}"))
        else:
            num_hint = f" ({extracted_num})" if extracted_num else ""
            # If we found a product the customer mentioned, acknowledge it by name
            if mentioned_products:
                mp = mentioned_products[0]
                mp_name = mp.get("title", "")
                lines.append(t(
                    f"I wasn't able to locate an order for the {mp_name} in our system. "
                    "Could you please share your order number? "
                    "You'll find it in your confirmation email.",
                    f"Não encontrei um pedido para {mp_name} no sistema. "
                    "Poderia informar o número do pedido? "
                    "Você encontrará no e-mail de confirmação da compra."
                ))
            else:
                lines.append(t(
                    f"I wasn't able to locate order{num_hint} in our system. "
                    "Could you double-check the order number and reply with it?",
                    f"Não consegui localizar o pedido{num_hint} no sistema. "
                    "Poderia confirmar o número do pedido e responder para que eu possa verificar?"
                ))

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: RETURN / EXCHANGE
    # ══════════════════════════════════════════════════════════════════════════
    elif category == "TROCA_DEVOLUCAO":
        on = order.get("order_number", "") if order else ""
        items_str = ", ".join(i["title"] for i in order.get("items", [])) if order else ""
        # Use richer product name from catalogue if available
        product_name_from_catalogue = product.get("title") if product else None
        display_items = product_name_from_catalogue or items_str
        if not order:
            # No order found — acknowledge product if mentioned, then ask for order number
            if mentioned_products:
                mp_name = mentioned_products[0].get("title", "")
                lines.append(t(
                    f"I can see you're requesting a return or exchange for the {mp_name}. "
                    "To verify your order and check eligibility under our 14-day return policy, "
                     "could you please provide your order number? "
                    "You'll find it in your original confirmation email.",
                    f"Vejo que você está solicitando a devolução ou troca do {mp_name}. "
                    "Para verificar seu pedido e checar a elegibilidade dentro do prazo de 14 dias, "
                    "poderia informar o número do pedido? "
                    "Você encontrará no e-mail de confirmação original."
                ))
            else:
                lines.append(t(
                    "I'd be happy to help with your return or exchange request. "
                    "To get started, could you please provide your order number? "
                    "You'll find it in your original confirmation email.",
                    "Ficaria feliz em ajudar com sua solicitação de devolução ou troca. "
                    "Para iniciarmos, poderia me informar o número do seu pedido? "
                    "Você encontrará no e-mail de confirmação original."
                ))

        elif return_window == "expired":
            # ── DENY: outside the 14-day window ──────────────────────────────
            days_txt = t(f"{days_elapsed} days", f"{days_elapsed} dias") if days_elapsed else ""
            window_txt = t(f"{rp['window_days']} calendar days",
                           f"{rp['window_days']} dias corridos")
            lines.append(t(
                f"I've located your order {on}.",
                f"Localizei seu pedido {on}."
            ))
            if display_items:
                lines.append(t(f"Items: {display_items}", f"Itens: {display_items}") + "\n")
            lines.append(t(
                f"Unfortunately, after carefully reviewing your request, I can see that your order "
                f"was delivered {days_txt} ago. Our return policy allows returns within {window_txt} "
                f"of confirmed delivery.",
                f"Após analisar cuidadosamente sua solicitação, verificamos que o pedido foi entregue "
                f"há {days_txt}. Nossa política permite devoluções dentro de {window_txt} após "
                f"a confirmação da entrega."
            ))
            lines.append("")
            lines.append(t(
                f"As the return window has passed, we are unfortunately unable to process this return. "
                f"We apologise for any inconvenience this may cause.",
                f"Como o prazo de devolução foi ultrapassado, infelizmente não conseguimos processar "
                f"esta solicitação. Pedimos desculpas pelo transtorno."
            ))
            lines.append("")
            lines.append(t(
                f"For full details, you can review our return policy here: {rp['url']}",
                f"Para mais detalhes, consulte nossa política de devoluções: {rp['url']}"
            ))

        elif return_window == "eligible":
            # ── APPROVE: within window — request photos + confirm email ───────
            lines.append(t(
                f"I've located your order {on} and I can see it was delivered recently.",
                f"Localizei seu pedido {on} e verifico que foi entregue recentemente."
            ))
            if display_items:
                lines.append(t(f"Items: {display_items}", f"Itens: {display_items}"))
            lines.append("")
            lines.append(t(
                "Your order is within our 14-day return window, so we're happy to assist! "
                "To process your return, we'll need a few things from you:",
                "Seu pedido está dentro do prazo de 14 dias para devolução, então podemos prosseguir! "
                "Para darmos continuidade, precisaremos de algumas informações:"
            ))
            lines.append("")
            lines.append(t(
                "1. Photos of the item(s) you wish to return, clearly showing the condition and any issues.",
                "1. Fotos dos itens que deseja devolver, mostrando claramente o estado e o problema identificado."
            ))
            lines.append(t(
                "2. Confirmation of the email address registered on the order "
                f"({'your email appears to be: ' + customer_email if customer_email else 'please provide the email used at checkout'}).",
                "2. Confirmação do endereço de e-mail cadastrado no pedido "
                f"({'seu e-mail parece ser: ' + customer_email if customer_email else 'por favor informe o e-mail utilizado na compra'})."
            ))
            lines.append(t(
                "3. The reason for the return (e.g. quality issue, wrong item, change of mind).",
                "3. O motivo da devolução (ex: problema de qualidade, item errado, desistência)."
            ))
            lines.append("")
            lines.append(t(
                f"Once we receive your photos and confirmation, we will issue a prepaid return label. "
                f"After the item is received at our warehouse, your refund will be processed within "
                f"{rp['refund_processing_days']} business days.",
                f"Após recebermos as fotos e confirmações, enviaremos uma etiqueta de devolução. "
                f"Assim que o item for recebido em nosso estoque, o reembolso será processado em até "
                f"{rp['refund_processing_days']} dias úteis."
            ))
            lines.append("")
            lines.append(t(
                f"For reference, you can review our full return policy here: {rp['url']}",
                f"Para referência, consulte nossa política de devoluções: {rp['url']}"
            ))

        elif return_window == "not_delivered":
            # ── Order exists but not yet delivered ────────────────────────────
            tracking = order.get("tracking", []) if order else []
            lines.append(t(
                f"I've located your order {on}. It appears your order is still in transit and "
                "hasn't been delivered yet.",
                f"Localizei seu pedido {on}. Parece que o pedido ainda está em trânsito e "
                "não foi entregue."
            ))
            if tracking and tracking[0].get("tracking_number"):
                tr = tracking[0]
                lines.append("")
                lines.append(t(f"Tracking number: {tr['tracking_number']}",
                               f"Código de rastreio: {tr['tracking_number']}"))
                if tr.get("tracking_url"):
                    lines.append(t(f"Track your package: {tr['tracking_url']}",
                                   f"Acompanhe sua entrega: {tr['tracking_url']}"))
            lines.append("")
            lines.append(t(
                "Once the delivery is confirmed, you'll have 14 days to request a return if needed. "
                "Please don't hesitate to reach out after it arrives.",
                "Após a confirmação da entrega, você terá 14 dias para solicitar a devolução, se necessário. "
                "Não hesite em nos contatar após o recebimento."
            ))

        else:
            # ── Unknown window (no delivery data) — ask for more info ─────────
            lines.append(t(
                "I'd be happy to help with your return or exchange. "
                "To check your eligibility under our 14-day return policy, could you please:\n\n"
                "1. Confirm your order number\n"
                "2. Let us know the delivery date\n"
                "3. Share photos of the item(s)\n\n"
                f"You can review our full return policy here: {rp['url']}",
                "Ficaria feliz em ajudar com sua devolução ou troca. "
                "Para verificar sua elegibilidade dentro do prazo de 14 dias, poderia:\n\n"
                "1. Confirmar o número do pedido\n"
                "2. Informar a data de entrega\n"
                "3. Enviar fotos dos itens\n\n"
                f"Consulte nossa política de devoluções: {rp['url']}"
            ))

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: CANCELLATION
    # ══════════════════════════════════════════════════════════════════════════
    elif category == "CANCELAMENTO":
        if order:
            on = order.get("order_number", "")
            fs = order.get("fulfillment_status", "unfulfilled")
            total = f"{order.get('total_price', '')} {order.get('currency', '')}".strip()
            items_str = ", ".join(i["title"] for i in order.get("items", []))
            # Prefer catalogue product name if available
            display_items_c = (product.get("title") if product else None) or items_str
            lines.append(t(f"I've located your order {on}.", f"Localizei seu pedido {on}."))
            if display_items_c:
                lines.append(t(f"Items: {display_items_c}", f"Itens: {display_items_c}"))
            if total:
                lines.append(t(f"Order total: {total}", f"Total do pedido: {total}") + "\n")
            if fs in ("unfulfilled", "partial"):
                lines.append(t(
                    "Your order has not been shipped yet, so cancellation may still be possible. "
                    "Please reply confirming you'd like to proceed with the cancellation.",
                    "Seu pedido ainda não foi enviado, então o cancelamento pode ser possível. "
                    "Responda confirmando que deseja prosseguir com o cancelamento."
                ))
            else:
                lines.append(t(
                    "Unfortunately, your order has already been shipped and can no longer be cancelled. "
                    "Once you receive it, you're welcome to initiate a return within 14 days of delivery.",
                    "Infelizmente, seu pedido já foi enviado e não pode mais ser cancelado. "
                    "Após o recebimento, você pode solicitar a devolução dentro de 14 dias da entrega."
                ))
                lines.append(t(
                    f"Return policy: {rp['url']}",
                    f"Política de devoluções: {rp['url']}"
                ))
        else:
            num_hint = f" ({extracted_num})" if extracted_num else ""
            lines.append(t(
                f"I wasn't able to locate order{num_hint}. "
                "Could you confirm the order number so I can check its status?",
                f"Não localizei o pedido{num_hint}. "
                "Poderia confirmar o número para que eu possa verificar o status?"
            ))

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: ORDER PROBLEM
    # ══════════════════════════════════════════════════════════════════════════
    elif category == "PROBLEMA_PEDIDO":
        if order:
            on = order.get("order_number", "")
            items_str = ", ".join(i["title"] for i in order.get("items", []))
            # Use richest product name available: catalogue > order items > hints
            display_items_p = (product.get("title") if product else None) or items_str
            lines.append(t(
                f"I'm sorry to hear about the issue with your order {on}. "
                "We sincerely apologise for this experience.",
                f"Lamento muito pelo problema com seu pedido {on}. "
                "Pedimos sinceras desculpas pelo transtorno."
            ))
            if display_items_p:
                lines.append(t(f"Items in order: {display_items_p}", f"Itens do pedido: {display_items_p}"))
            lines.append("")
            lines.append(t(
                "To resolve this as quickly as possible, could you please send us:\n\n"
                "1. Clear photos of the item(s) showing the problem\n"
                "2. A brief description of the issue\n"
                f"3. Confirmation that the email on file is correct ({customer_email or 'please provide'})",
                "Para resolver isso o mais rápido possível, poderia nos enviar:\n\n"
                "1. Fotos nítidas dos itens com o problema\n"
                "2. Uma breve descrição do problema\n"
                f"3. Confirmação de que o e-mail cadastrado está correto ({customer_email or 'por favor informe'})"
            ))
            lines.append("")
            if return_window == "eligible":
                lines.append(t(
                    f"Since your order is within our {rp['window_days']}-day return window, "
                    "we can arrange a replacement or full refund once we receive your photos.",
                    f"Como seu pedido está dentro do prazo de {rp['window_days']} dias, "
                    "podemos providenciar a troca ou reembolso após recebermos as fotos."
                ))
            elif return_window == "expired":
                lines.append(t(
                    "Although the standard return window has passed, we take product quality "
                    "very seriously. Please send the photos and we will evaluate your case individually.",
                    "Embora o prazo padrão de devolução tenha passado, levamos a qualidade muito a sério. "
                    "Por favor, envie as fotos e avaliaremos seu caso individualmente."
                ))
        else:
            num_hint = f" ({extracted_num})" if extracted_num else ""
            # If we recognised a product name from the email, reference it explicitly
            if mentioned_products:
                mp_name = mentioned_products[0].get("title", "")
                lines.append(t(
                    f"I'm sorry to hear about the issue with your {mp_name}. "
                    "To help you as quickly as possible, could you please share:\n\n"
                    "1. Your order number\n"
                    "2. Clear photos of the issue\n"
                    "3. A brief description of the problem",
                    f"Lamento muito pelo problema com seu {mp_name}. "
                    "Para ajudá-lo o mais rápido possível, poderia compartilhar:\n\n"
                    "1. Número do pedido\n"
                    "2. Fotos nítidas do problema\n"
                    "3. Uma breve descrição do que aconteceu"
                ))
            else:
                lines.append(t(
                    f"I'm sorry to hear about this issue{num_hint}. "
                    "To help you, could you please share:\n\n"
                    "1. Your order number\n"
                    "2. Photos of the issue\n"
                    "3. A description of the problem",
                    f"Lamento muito pelo problema{num_hint}. "
                    "Para ajudá-lo, poderia compartilhar:\n\n"
                    "1. Número do pedido\n"
                    "2. Fotos do problema\n"
                    "3. Uma descrição do que aconteceu"
                ))

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: PRODUCT QUESTION
    # ══════════════════════════════════════════════════════════════════════════
    elif category == "DUVIDA_PRODUTO":
        if product:
            lines.append(t(f"Here is the information about {product.get('title', '')}:\n",
                           f"Aqui estão as informações sobre {product.get('title', '')}:\n"))
            price = product["variants"][0]["price"] if product.get("variants") else "N/A"
            lines.append(t(f"Price: {price}", f"Preço: R$ {price}"))
            avail = product.get("available")
            lines.append(t(f"Availability: {'In stock' if avail else 'Out of stock'}",
                           f"Disponibilidade: {'Em estoque' if avail else 'Indisponível'}"))
            if product.get("variants") and len(product["variants"]) > 1:
                vs = ", ".join(v["title"] for v in product["variants"] if v["title"] != "Default Title")
                if vs:
                    lines.append(t(f"Options: {vs}", f"Opções: {vs}"))
            if product.get("url"):
                lines.append(t(f"\nView on our store: {product['url']}",
                               f"\nVeja na nossa loja: {product['url']}"))
        else:
            lines.append(t(
                f"Thank you for your interest! Could you let me know the exact product name "
                f"or share the link? You can also browse our full collection at {cfg['store_url']}.",
                f"Obrigado pelo interesse! Poderia me dizer o nome exato do produto "
                f"ou compartilhar o link? Você também pode ver nossa coleção em {cfg['store_url']}."
            ))

    # ══════════════════════════════════════════════════════════════════════════
    # CATEGORY: OTHER
    # ══════════════════════════════════════════════════════════════════════════
    else:
        lines.append(t(
            "I've received your message and will look into it right away. "
            "I'll get back to you as soon as possible.",
            "Recebi sua mensagem e vou analisar imediatamente. "
            "Retornarei em breve com mais informações."
        ))

    lines.extend([f"\n{help_line}\n", closing])
    return "\n".join(lines)


# ─── HTTP Request Handler ───────────────────────────────────────────────────

class SupportHandler(SimpleHTTPRequestHandler):
    """Handle API routes and serve static files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="static", **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if path == "/api/tickets":
            tickets = database.list_tickets(
                status=params.get("status"),
                category=params.get("category"),
                search=params.get("search"),
                limit=int(params.get("limit", 50)),
            )
            self._json_response(tickets)

        elif path.startswith("/api/tickets/") and path.count("/") == 3:
            tid = int(path.split("/")[3])
            ticket = database.get_ticket(tid)
            if ticket:
                self._json_response(ticket)
            else:
                self._json_response({"error": "Not found"}, 404)

        elif path == "/api/stats":
            self._json_response(database.get_stats())

        elif path == "/api/shopify/orders":
            try:
                result = shopify_client.search_orders(params.get("search", ""))
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif path == "/api/shopify/products":
            try:
                result = shopify_client.search_products(params.get("search", ""))
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif path == "/api/shopify/policies":
            try:
                result = shopify_client.get_policies()
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif path == "/api/shopify/customers":
            try:
                result = shopify_client.search_customers(params.get("email", ""))
                self._json_response(result)
            except Exception as e:
                self._json_response({"error": str(e)}, 500)

        elif path == "/api/status":
            gmail_ok = False
            gmail_msg = "gmail_credentials.json not found"
            if GMAIL_AVAILABLE:
                try:
                    import gmail_client as _gc
                    if os.path.exists(_gc.TOKEN_FILE):
                        from google.oauth2.credentials import Credentials as _Creds
                        _creds = _Creds.from_authorized_user_file(_gc.TOKEN_FILE, _gc.SCOPES)
                        gmail_ok = bool(_creds and (_creds.valid or _creds.refresh_token))
                        gmail_msg = "token valid" if _creds.valid else "token expired — will auto-refresh"
                    elif os.path.exists(_gc.CREDENTIALS_FILE):
                        gmail_msg = "credentials.json found — run sync to complete OAuth"
                    else:
                        gmail_msg = "Missing gmail_credentials.json — see setup instructions"
                except Exception as e:
                    gmail_msg = str(e)

            shopify_ok = bool(os.getenv("SHOPIFY_STORE") and os.getenv("SHOPIFY_ACCESS_TOKEN") and
                              "your_" not in os.getenv("SHOPIFY_ACCESS_TOKEN", ""))
            smtp_ok = smtp_ready()
            self._json_response({
                "gmail": {"ok": gmail_ok, "msg": gmail_msg, "available": GMAIL_AVAILABLE},
                "smtp":  {"ok": smtp_ok, "user": os.getenv("GMAIL_USER", "")},
                "shopify": {"ok": shopify_ok, "store": os.getenv("SHOPIFY_STORE", "")},
                "db": {"ok": True, "path": database.DB_PATH},
            })

        elif path == "/api/agent-config":
            # Expose AGENT_CONFIG for the settings panel in the dashboard
            self._json_response(AGENT_CONFIG)

        elif path == "/" or path == "/index.html":
            self.path = "/index.html"
            super().do_GET()
        else:
            super().do_GET()

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/tickets/") and path.count("/") == 3:
            tid = int(path.split("/")[3])
            body = self._read_body()

            ticket = database.get_ticket(tid)
            if not ticket:
                self._json_response({"error": "Not found"}, 404)
                return

            updates = {}
            if "draft_response" in body:
                updates["draft_response"] = body["draft_response"]
                database.add_log(tid, "edited", "Draft manually edited")

            if "status" in body:
                updates["status"] = body["status"]
                if body["status"] == "approved":
                    database.add_log(tid, "approved", f"From {ticket['status']}")
                elif body["status"] == "rejected":
                    database.add_log(tid, "rejected", f"From {ticket['status']}")

            updated = database.update_ticket(tid, updates)
            self._json_response(updated)
        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/fetch-emails":
            self._handle_fetch_emails()

        elif path == "/api/compose":
            self._handle_compose()

        elif path.endswith("/send") and "/api/tickets/" in path:
            parts = path.split("/")
            tid = int(parts[3])
            self._handle_send(tid)

        elif path.endswith("/process") and "/api/tickets/" in path:
            parts = path.split("/")
            tid = int(parts[3])
            self._handle_process_ticket(tid)

        else:
            self._json_response({"error": "Not found"}, 404)

    def _handle_process_ticket(self, ticket_id: int):
        """Re-process a ticket: query Shopify + regenerate draft response."""
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            self._json_response({"error": "Not found"}, 404)
            return
        try:
            shopify_data = query_shopify(ticket)
            ticket["shopify_data"] = shopify_data
            draft = generate_draft(ticket)
            now = datetime.now(timezone.utc).isoformat()
            database.update_ticket(ticket_id, {
                "shopify_data": shopify_data,
                "shopify_order_number": shopify_data.get("order_number") or ticket.get("shopify_order_number"),
                "draft_response": draft,
                "status": "draft_ready",
                "processed_at": now,
            })
            database.add_log(ticket_id, "shopify_queried", json.dumps(shopify_data, default=str)[:500])
            database.add_log(ticket_id, "draft_generated", "Re-processed via API")
            self._json_response({"success": True, "ticket_id": ticket_id, "order_number": shopify_data.get("order_number"), "draft_length": len(draft)})
        except Exception as e:
            self._json_response({"error": str(e)}, 500)

    def _handle_fetch_emails(self):
        gmail_ready = (
            GMAIL_AVAILABLE and
            (os.path.exists(gmail_client.TOKEN_FILE) or os.path.exists(gmail_client.CREDENTIALS_FILE))
        )
        if not gmail_ready:
            self._json_response({"processed": 0, "warning": "Gmail not configured — no credentials found."})
            return

        try:
            emails = gmail_client.fetch_unread_emails(max_results=10)
        except Exception as e:
            self._json_response({"processed": 0, "warning": f"Gmail error: {str(e)}"})
            return

        new_tickets = []
        for email in emails:
            if database.ticket_exists(email["id"]):
                continue

            customer_name = extract_name(email["from"])
            customer_email = extract_email(email["from"])
            lang = detect_language(email["body"])
            category = classify_email(email["subject"], email["body"])

            ticket_data = {
                "gmail_message_id": email["id"],
                "gmail_thread_id": email["thread_id"],
                "customer_email": customer_email,
                "customer_name": customer_name,
                "subject": email["subject"],
                "body": email["body"],
                "language": lang,
                "category": category,
                "status": "processing",
            }

            ticket = database.create_ticket(ticket_data)
            database.add_log(ticket["id"], "created", f"From: {email['from']}")
            database.add_log(ticket["id"], "classified", f"Category: {category}")

            # Query Shopify
            shopify_data = query_shopify(ticket)
            ticket["shopify_data"] = shopify_data

            # Generate draft
            draft = generate_draft(ticket)

            database.update_ticket(ticket["id"], {
                "shopify_data": shopify_data,
                "shopify_order_number": shopify_data.get("order_number"),
                "status": "draft_ready",
                "processed_at": datetime.now(timezone.utc).isoformat(),
            })
            database.update_ticket(ticket["id"], {"draft_response": draft})
            database.add_log(ticket["id"], "shopify_queried", json.dumps(shopify_data, default=str)[:500])
            database.add_log(ticket["id"], "draft_generated", "Auto-generated")

            try:
                gmail_client.mark_as_read(email["id"])
            except Exception:
                pass

            new_tickets.append(database.get_ticket(ticket["id"]))

        self._json_response({"processed": len(new_tickets), "tickets": new_tickets})

    def _handle_compose(self):
        """Save an outbound email as a ticket so it appears in the inbox."""
        body = self._read_body()
        to = body.get("to", "").strip()
        name = body.get("name", "").strip() or to.split("@")[0]
        subject = body.get("subject", "").strip()
        text = body.get("body", "").strip()

        if not to or not subject or not text:
            self._json_response({"error": "Missing required fields: to, subject, body"}, 400)
            return

        now = datetime.now(timezone.utc).isoformat()
        msg_id = f"outbound-{int(datetime.now(timezone.utc).timestamp()*1000)}"

        ticket_data = {
            "gmail_message_id": msg_id,
            "gmail_thread_id": msg_id,
            "customer_email": to,
            "customer_name": name,
            "subject": subject,
            "body": f"[Outbound] {text}",
            "language": detect_language(text),
            "category": "OUTRO",
            "status": "approved",
            "received_at": now,
        }

        ticket = database.create_ticket(ticket_data)
        database.update_ticket(ticket["id"], {
            "draft_response": text,
            "processed_at": now,
        })
        database.add_log(ticket["id"], "composed", f"Outbound to {to}")
        self._json_response({"success": True, "ticket": database.get_ticket(ticket["id"])})

    def _handle_send(self, ticket_id: int):
        try:
            ticket = database.get_ticket(ticket_id)
            if not ticket:
                self._json_response({"error": "Not found"}, 404)
                return

            response_text = ticket.get("final_response") or ticket.get("draft_response")
            if not response_text:
                self._json_response({"error": "No response to send"}, 400)
                return

            subject = ticket.get("subject", "")
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # ── 1. Try SMTP (App Password) — the primary send method ──────────
            if smtp_ready():
                result = send_email_smtp(
                    to=ticket["customer_email"],
                    subject=subject,
                    body=response_text,
                    thread_id=ticket.get("gmail_thread_id"),
                )
                if result["ok"]:
                    now_sent = datetime.now(timezone.utc).isoformat()
                    database.update_ticket(ticket_id, {
                        "status": "sent",
                        "sent_at": now_sent,
                    })
                    database.add_log(ticket_id, "sent", f"SMTP → {ticket['customer_email']}")
                    # ── Create outbox copy so it appears in the Sent tab ──
                    sent_copy_id = f"sent-{ticket.get('gmail_message_id', ticket_id)}"
                    if not database.ticket_exists(sent_copy_id):
                        database.create_ticket({
                            "gmail_message_id": sent_copy_id,
                            "gmail_thread_id": ticket.get("gmail_thread_id", ""),
                            "customer_email": ticket.get("customer_email", ""),
                            "customer_name": ticket.get("customer_name", ""),
                            "subject": subject,
                            "body": response_text,
                            "language": ticket.get("language", "en"),
                            "category": ticket.get("category", "OUTRO"),
                            "shopify_order_number": ticket.get("shopify_order_number"),
                            "status": "sent",
                            "received_at": now_sent,
                        })
                        database.update_ticket(
                            database.get_ticket_by_message_id(sent_copy_id)["id"],
                            {"sent_at": now_sent, "draft_response": response_text},
                        )
                    self._json_response({"success": True, "ticket": database.get_ticket(ticket_id)})
                    return
                else:
                    # SMTP configured but failed (wrong password, network, etc.)
                    database.add_log(ticket_id, "send_error", result["error"])
                    self._json_response({"error": result["error"]})
                    return

            # ── 2. SMTP not configured — return draft for manual copy/paste ───
            database.update_ticket(ticket_id, {"status": "approved"})
            database.add_log(ticket_id, "manual_send", "SMTP not configured — send manually")
            self._json_response({
                "success": True,
                "manual": True,
                "draft": response_text,
                "to": ticket["customer_email"],
                "subject": subject,
                "ticket": database.get_ticket(ticket_id),
            })

        except Exception as exc:
            # Safety net — prevent any internal error (including stale gmail_client
            # calls) from leaking confusing credential messages to the UI.
            err_str = str(exc)
            try:
                database.add_log(ticket_id, "send_error", err_str)
            except Exception:
                pass
            # If SMTP is configured the error is real (auth failure, network, etc.)
            if smtp_ready():
                self._json_response({"error": f"Erro ao enviar: {err_str}"})
            else:
                # SMTP not yet configured — show the manual copy dialog instead
                try:
                    t2 = database.get_ticket(ticket_id) or {}
                    draft = t2.get("final_response") or t2.get("draft_response") or ""
                    subj = t2.get("subject", "")
                    if subj and not subj.lower().startswith("re:"):
                        subj = f"Re: {subj}"
                    self._json_response({
                        "success": True,
                        "manual": True,
                        "draft": draft,
                        "to": t2.get("customer_email", ""),
                        "subject": subj,
                        "ticket": t2,
                    })
                except Exception:
                    self._json_response({"error": "Erro interno. Reinicie o servidor e tente novamente."})

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        if "/api/" in str(args[0]) if args else False:
            print(f"  → {args[0]}")


# ─── Threaded HTTP Server ────────────────────────────────────────────────────

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server — prevents Gmail OAuth from blocking requests."""
    daemon_threads = True


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    database.init_db()

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = int(os.getenv("APP_PORT", 8000))

    # Check Gmail token status
    gmail_status = "✗ not configured"
    if GMAIL_AVAILABLE:
        try:
            import gmail_client as _gc
            if os.path.exists(_gc.TOKEN_FILE):
                gmail_status = "✓ token found"
            elif os.path.exists(_gc.CREDENTIALS_FILE):
                gmail_status = "⚠  credentials.json found — OAuth needed on first sync"
            else:
                gmail_status = "✗ gmail_credentials.json missing (see README)"
        except Exception:
            pass

    print(f"""
  ✦  Monteiro Jewels — Support Agent Dashboard
  ─────────────────────────────────────────────
  Dashboard:  http://localhost:{port}
  Shopify:    {os.getenv('SHOPIFY_STORE', 'not configured')}
  Gmail:      {gmail_status}
  Database:   {database.DB_PATH}
  ─────────────────────────────────────────────
  Press Ctrl+C to stop
""")

    server = ThreadedHTTPServer((host, port), SupportHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Servidor encerrado.")
        server.server_close()


if __name__ == "__main__":
    main()
