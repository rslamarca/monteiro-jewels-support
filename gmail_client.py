"""
Gmail API client using OAuth2.
Handles reading emails, creating drafts, and sending replies.
"""
import os
import json
import base64
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.modify",
]

TOKEN_FILE = "gmail_token.json"
CREDENTIALS_FILE = "gmail_credentials.json"


def get_gmail_service():
    """Build and return an authenticated Gmail service."""
    creds = None

    # Load existing token if available
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Option 1: credentials JSON file (preferred)
            if os.path.exists(CREDENTIALS_FILE):
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)

            # Option 2: env variables GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET
            elif os.getenv("GMAIL_CLIENT_ID") and os.getenv("GMAIL_CLIENT_SECRET") \
                    and not os.getenv("GMAIL_CLIENT_ID", "").startswith("your_"):
                client_config = {
                    "installed": {
                        "client_id": os.getenv("GMAIL_CLIENT_ID"),
                        "client_secret": os.getenv("GMAIL_CLIENT_SECRET"),
                        "redirect_uris": ["http://localhost"],
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                }
                flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

            else:
                raise FileNotFoundError(
                    "Gmail credentials not found. Either:\n"
                    "  1) Download 'gmail_credentials.json' from Google Cloud Console and place it in the app folder, OR\n"
                    "  2) Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in the .env file.\n"
                    "See README.md for step-by-step instructions."
                )

            creds = flow.run_local_server(port=8090, open_browser=True)

        # Save refreshed/new token
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ─── Read Emails ─────────────────────────────────────────────────────────────

def fetch_unread_emails(max_results: int = 20, label: str = "INBOX") -> list[dict]:
    """Fetch unread emails from inbox."""
    service = get_gmail_service()
    results = service.users().messages().list(
        userId="me",
        q=f"is:unread label:{label}",
        maxResults=max_results,
    ).execute()

    messages = results.get("messages", [])
    emails = []
    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        emails.append(_parse_message(msg))

    return emails


def fetch_email_by_id(message_id: str) -> dict:
    """Fetch a specific email by message ID."""
    service = get_gmail_service()
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()
    return _parse_message(msg)


def fetch_thread(thread_id: str) -> list[dict]:
    """Fetch all messages in a thread."""
    service = get_gmail_service()
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    return [_parse_message(msg) for msg in thread.get("messages", [])]


def _parse_message(msg: dict) -> dict:
    """Parse Gmail message into a clean dict."""
    headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
    body = _extract_body(msg["payload"])

    return {
        "id": msg["id"],
        "thread_id": msg["threadId"],
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "date": headers.get("date", ""),
        "body": body,
        "labels": msg.get("labelIds", []),
        "snippet": msg.get("snippet", ""),
    }


def _extract_body(payload: dict) -> str:
    """Extract text body from message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
        if part.get("parts"):
            result = _extract_body(part)
            if result:
                return result

    # Fallback: try HTML
    for part in parts:
        if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")

    return ""


# ─── Send / Draft ────────────────────────────────────────────────────────────

def create_draft(to: str, subject: str, body: str, thread_id: Optional[str] = None) -> dict:
    """Create a Gmail draft."""
    service = get_gmail_service()
    message = _create_message(to, subject, body)

    draft_body = {"message": message}
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=draft_body).execute()
    return {"draft_id": draft["id"], "message_id": draft["message"]["id"]}


def send_draft(draft_id: str) -> dict:
    """Send an existing draft."""
    service = get_gmail_service()
    result = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
    return {"message_id": result["id"], "thread_id": result["threadId"]}


def send_reply(to: str, subject: str, body: str, thread_id: str, message_id: str) -> dict:
    """Send a reply to an existing thread."""
    service = get_gmail_service()
    message = _create_message(to, f"Re: {subject}", body)
    message["threadId"] = thread_id

    # Add In-Reply-To header
    raw = base64.urlsafe_b64decode(message["raw"])
    raw_str = raw.decode("utf-8")
    raw_str = raw_str.replace("\n\n", f"\nIn-Reply-To: {message_id}\nReferences: {message_id}\n\n", 1)
    message["raw"] = base64.urlsafe_b64encode(raw_str.encode("utf-8")).decode("utf-8")

    result = service.users().messages().send(userId="me", body=message).execute()
    return {"message_id": result["id"], "thread_id": result["threadId"]}


def mark_as_read(message_id: str):
    """Remove UNREAD label from a message."""
    service = get_gmail_service()
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def _create_message(to: str, subject: str, body: str) -> dict:
    """Create a raw email message."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}
