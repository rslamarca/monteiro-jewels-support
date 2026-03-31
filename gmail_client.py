"""
Gmail IMAP client using App Password (no OAuth2 required).

Reads emails via imaplib (Python stdlib); sending is handled by SMTP in main.py.
Set GMAIL_USER and GMAIL_APP_PASSWORD environment variables to enable.
"""
import os
import json
import email
import imaplib
import base64
from typing import Optional
from email.header import decode_header as _decode_header

# ─── Constants (kept for backward compat with main.py checks) ─────────────────
# TOKEN_FILE lives in /tmp so it is always writable on cloud hosts (Render, etc.)
TOKEN_FILE       = "/tmp/gmail_token.json"
CREDENTIALS_FILE = "gmail_credentials.json"
SCOPES           = []   # Not used in IMAP mode; kept so main.py imports cleanly

_GMAIL_USER = os.getenv("GMAIL_USER", "")
_GMAIL_PWD  = os.getenv("GMAIL_APP_PASSWORD", "")


def _imap_ready() -> bool:
    return bool(_GMAIL_USER and _GMAIL_PWD
                and "your_" not in _GMAIL_PWD
                and len(_GMAIL_PWD.replace(" ", "")) >= 16)


# ─── Create a marker file so main.py's os.path.exists(TOKEN_FILE) passes ──────
def _ensure_marker():
    if not _imap_ready():
        return
    if not os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "w") as f:
                json.dump({"type": "imap", "user": _GMAIL_USER}, f)
        except Exception:
            pass

_ensure_marker()


# ─── IMAP helpers ──────────────────────────────────────────────────────────────

def _imap_connect() -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP4_SSL connection to Gmail."""
    if not _imap_ready():
        raise RuntimeError(
            "Gmail not configured. Set GMAIL_USER and GMAIL_APP_PASSWORD "
            "(use a 16-char App Password, not your regular password)."
        )
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(_GMAIL_USER, _GMAIL_PWD)
    return mail


def _decode_str(value: str) -> str:
    """Decode RFC 2047-encoded header value."""
    if not value:
        return ""
    parts = _decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body, falling back to HTML if needed."""
    if msg.is_multipart():
        # Prefer text/plain
        for part in msg.walk():
            ct  = part.get_content_type()
            cd  = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace")
        # Fallback: HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


def _parse_imap_message(uid: bytes, raw: bytes) -> dict:
    """Parse raw RFC 822 bytes into a clean dict matching the original API."""
    msg      = email.message_from_bytes(raw)
    body     = _extract_body(msg)
    msg_id   = msg.get("Message-ID", uid.decode()).strip()
    thread_id = msg.get("Thread-Index", msg_id)

    return {
        "id":        uid.decode(),   # IMAP UID — used for mark_as_read
        "thread_id": thread_id,
        "from":      _decode_str(msg.get("From", "")),
        "to":        msg.get("To", ""),
        "subject":   _decode_str(msg.get("Subject", "")),
        "date":      msg.get("Date", ""),
        "body":      body,
        "labels":    ["INBOX", "UNREAD"],
        "snippet":   body[:200].replace("\n", " "),
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch_unread_emails(max_results: int = 20, label: str = "INBOX") -> list:
    """Fetch unread emails from Gmail via IMAP."""
    mail = _imap_connect()
    try:
        mail.select("INBOX")
        _, data = mail.search(None, "UNSEEN")
        uids = data[0].split()
        uids = uids[-max_results:]   # keep most recent N

        emails = []
        for uid in reversed(uids):   # newest first
            _, msg_data = mail.fetch(uid, "(RFC822)")
            if msg_data and msg_data[0]:
                emails.append(_parse_imap_message(uid, msg_data[0][1]))
        return emails
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_email_by_id(message_id: str) -> dict:
    """Fetch a specific email by IMAP UID."""
    mail = _imap_connect()
    try:
        mail.select("INBOX")
        _, msg_data = mail.fetch(message_id.encode(), "(RFC822)")
        return _parse_imap_message(message_id.encode(), msg_data[0][1])
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_thread(thread_id: str) -> list:
    """Fetch messages in a thread (simplified: returns the single message)."""
    try:
        return [fetch_email_by_id(thread_id)]
    except Exception:
        return []


def mark_as_read(message_id: str):
    """Mark an email as read by setting the \\Seen IMAP flag."""
    try:
        mail = _imap_connect()
        try:
            mail.select("INBOX")
            mail.store(message_id.encode(), "+FLAGS", "\\Seen")
        finally:
            mail.logout()
    except Exception:
        pass   # Non-critical — email was processed even if flag fails


# ─── Stubs (sending is handled by SMTP in main.py) ───────────────────────────

def create_draft(to: str, subject: str, body: str,
                 thread_id: Optional[str] = None) -> dict:
    """Sending handled by SMTP in main.py; this is a no-op stub."""
    return {"draft_id": "smtp-mode", "message_id": "smtp-mode"}


def send_draft(draft_id: str) -> dict:
    """Sending handled by SMTP in main.py; this is a no-op stub."""
    return {"message_id": "smtp-mode", "thread_id": "smtp-mode"}


def send_reply(to: str, subject: str, body: str,
               thread_id: str, message_id: str) -> dict:
    """Sending handled by SMTP in main.py; this is a no-op stub."""
    return {"message_id": "smtp-mode", "thread_id": thread_id}
