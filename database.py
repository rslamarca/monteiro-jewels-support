"""
Database layer â supports both:
  â¢ Supabase / PostgreSQL  (when DATABASE_URL env var is set)
  â¢ Local SQLite           (fallback for Mac dev)
"""
import os
import json
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL", "")
_USE_PG = DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")

if _USE_PG:
    import psycopg2
    import psycopg2.extras
    PH = "%s"
else:
    import sqlite3
    PH = "?"
    DB_PATH = os.getenv("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "support.db"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_connection():
    if _USE_PG:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _commit_close(conn):
    conn.commit()
    conn.close()


def _row_to_dict(row):
    if row is None: return None
    d = dict(row)
    if not _USE_PG and d.get("shopify_data") and isinstance(d["shopify_data"], str):
        try: d["shopify_data"] = json.loads(d["shopify_data"])
        except: pass
    return d


def init_db():
    if _USE_PG:
        print("  â Database: Supabase PostgreSQL")
        return
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS support_tickets (id INTEGER PRIMARY KEY AUTOINCREMENT, gmail_message_id TEXT UNIQUE, gmail_thread_id TEXT, customer_email TEXT, customer_name TEXT, subject TEXT, body TEXT, language TEXT DEFAULT 'pt-BR', category TEXT, shopify_order_number TEXT, shopify_data TEXT, draft_response TEXT, final_response TEXT, status TEXT DEFAULT 'new', received_at TEXT, processed_at TEXT, approved_at TEXT, sent_at TEXT, created_at TEXT, updated_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON support_tickets(status);
        CREATE TABLE IF NOT EXISTS ticket_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER REFERENCES support_tickets(id), action TEXT, details TEXT, created_at TEXT);
    """)
    conn.commit()
    conn.close()
    print(f"  â Database: {DB_PATH}")


def _shopify_value(data):
    if data is None: return None
    if _USE_PG: return psycopg2.extras.Json(data)
    return json.dumps(data)


def create_ticket(data):
    conn = get_connection()
    now = _now()
    sql = f"INSERT INTO support_tickets (gmail_message_id, gmail_thread_id, customer_email, customer_name, subject, body, language, category, shopify_order_number, shopify_data, draft_response, final_response, status, received_at, processed_at, created_at, updated_at) VALUES ({',~'.join([PH]*17)}) {'RETURNING id' if _USE_PG else ''}"
    values = (data.get("gmail_message_id"), data.get("gmail_thread_id"), data.get("customer_email"), data.get("customer_name"), data.get("subject"), data.get("body"), data.get("language", "pt-BR"), data.get("category"), data.get("shopify_order_number"), _shopify_value(data.get("shopify_data")), data.get("draft_response"), data.get("final_response"), data.get("status", "new"), data.get("received_at", now), data.get("processed_at"), now, now)
    if _USE_PG:
        cur = conn.cursor(); cur.execute(sql, values); ticket_id = cur.fetchone()["id"]; conn.commit(); ticket = get_ticket(ticket_id, conn); conn.close()
    else:
        cursor = conn.execute(sql, values); ticket_id = cursor.lastrowid; conn.commit(); ticket = get_ticket(ticket_id, conn); conn.close()
    return ticket


def get_ticket(ticket_id, conn=None):
    close = conn is None
    if conn is None: conn = get_connection()
    if _USE_PG:
        cur = conn.cursor(); cur.execute(f"SELECT * FROM support_tickets WHERE id = {PH}", (ticket_id,)); row = cur.fetchone()
    else:
        row = conn.execute(f"SELECT * FROM support_tickets WHERE id = {PH}", (ticket_id,)).fetchone()
    if close: conn.close()
    return _row_to_dict(row)


def list_tickets(status=None, category=None, search=None, limit=50):
    conn = get_connection()
    query = "SELECT * FROM support_tickets WHERE 1=1"
    params = []
    if status: query += f" AND status = {PH}"; params.append(status)
    if category: query += f" AND category = {PH}"; params.append(category)
    query += f" ORDER BY received_at DESC LIMIT {PH}"; params.append(limit)
    if _USE_PG:
        cur = conn.cursor(); cur.execute(query, params); rows = cur.fetchall()
    else:
        rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def update_ticket(ticket_id, updates):
    conn = get_connection()
    now = _now()
    sets = [f"updated_at = {PH}"]; params = [now]
    for field in ["draft_response", "final_response", "status", "category", "processed_at", "sent_at", "shopify_order_number"]:
        if field in updates: sets.append(f"{rfield} = {PH}"); params.append(updates[field])
    if "status" in updates:
        if updates["status"] == "approved": sets.append(f"approved_at = {PH}"); params.append(now)
        elif updates["status"] == "sent": sets.append(f"sent_at = {PH}"); params.append(now)
    if "shopify_data" in updates: sets.append(f"shopify_data = {PH}"); params.append(_shopify_value(updates["shopify_data"]))
    params.append(ticket_id)
    sql = f"UPDATE support_tickets SET {', '.join(sets)} WHERE id = {PH}"
    if _USE_PG:
        cur = conn.cursor(); cur.execute(sql, params); conn.commit(); ticket = get_ticket(ticket_id, conn); conn.close()
    else:
        conn.execute(sql, params); conn.commit(); ticket = get_ticket(ticket_id, conn); conn.close()
    return ticket


def ticket_exists(id):
    conn = get_connection()
    row = conn.execute(f"SELECT 1 FROM support_tickets WHERE gmail_message_id = {PH}", (id,)).fetchone()
    conn.close()
    return row is not None


def get_stats():
    conn = get_connection()
    def _c(x): return conn.execute(x).fetchone()[0]
    result = {"total": _c("SELECT COUNT(*) FROM support_tickets"), "pending": _c("SELECT COUNT(*) FROM support_tickets WHERE status IN ('new','processing','draft_ready')"), "sent": _c("SELECT COUNT(*) FROM support_tickets WHERE status = 'sent'")}
    conn.close()
    return result


def add_log(ticket_id, action, details=None):
    conn = get_connection()
    conn.execute(f"INSERT INTO ticket_logs (ticket_id,action,details,created_at) VALUES ({PH},{PH},{PH},{PH})", (ticket_id, action, details, _now()))
    _commit_close(conn)
