import hashlib
import hmac
import json
import os
import smtplib
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "careconnect.db"
SAFE_DETAIL_KEYS = {"reason", "status_code", "count", "window_minutes", "configuration"}


def _db_path():
    path = Path(os.environ.get("SECURITY_EVENT_DB_PATH", os.environ.get("SEARCH_HISTORY_DB_PATH", DEFAULT_DB_PATH)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect():
    connection = sqlite3.connect(_db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def initialize_security_events():
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                endpoint TEXT,
                actor_hash TEXT,
                source_hash TEXT,
                details TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_security_event_type_time
                ON security_events(event_type, created_at);
            CREATE TABLE IF NOT EXISTS security_alerts (
                alert_key TEXT PRIMARY KEY,
                last_sent_at INTEGER NOT NULL
            );
            """
        )


def pseudonymize(value):
    if not value:
        return None
    key = os.environ.get("SECURITY_EVENT_HASH_KEY", "")
    if not key:
        return "hash-key-not-configured"
    return hmac.new(key.encode(), str(value).encode(), hashlib.sha256).hexdigest()[:20]


def _safe_details(details):
    details = details or {}
    return {key: details[key] for key in SAFE_DETAIL_KEYS if key in details}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def record_security_event(event_type, severity="info", endpoint=None, actor=None, source=None, details=None):
    initialize_security_events()
    safe = _safe_details(details)
    with _connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO security_events
                (created_at, event_type, severity, endpoint, actor_hash, source_hash, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (_utc_now(), event_type[:80], severity[:20], (endpoint or "")[:120],
             pseudonymize(actor), pseudonymize(source), json.dumps(safe)),
        )
        event_id = cursor.lastrowid

    if severity in {"high", "critical"}:
        _queue_alert(event_id, event_type, severity, endpoint, safe)
    return event_id


def count_recent(event_type, source=None, minutes=5):
    cutoff = datetime.fromtimestamp(time.time() - minutes * 60, timezone.utc).isoformat()
    query = "SELECT COUNT(*) FROM security_events WHERE event_type = ? AND created_at >= ?"
    params = [event_type, cutoff]
    if source:
        query += " AND source_hash = ?"
        params.append(pseudonymize(source))
    with _connect() as connection:
        return connection.execute(query, params).fetchone()[0]


def recent_events(limit=50):
    limit = max(1, min(int(limit), 200))
    with _connect() as connection:
        rows = connection.execute(
            """
            SELECT id, created_at, event_type, severity, endpoint, actor_hash, source_hash, details
            FROM security_events ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) | {"details": json.loads(row["details"])} for row in rows]


def security_summary():
    cutoff = datetime.fromtimestamp(time.time() - 24 * 60 * 60, timezone.utc).isoformat()
    with _connect() as connection:
        counts = connection.execute(
            """
            SELECT severity, COUNT(*) AS count FROM security_events
            WHERE created_at >= ? GROUP BY severity
            """,
            (cutoff,),
        ).fetchall()
    return {"last_24_hours": {row["severity"]: row["count"] for row in counts}, "recent": recent_events(20)}


def _alert_allowed(alert_key):
    cooldown = int(os.environ.get("SECURITY_ALERT_COOLDOWN_SECONDS", "900"))
    now = int(time.time())
    with _connect() as connection:
        row = connection.execute("SELECT last_sent_at FROM security_alerts WHERE alert_key = ?", (alert_key,)).fetchone()
        if row and now - row["last_sent_at"] < cooldown:
            return False
        connection.execute(
            "INSERT INTO security_alerts(alert_key, last_sent_at) VALUES(?, ?) "
            "ON CONFLICT(alert_key) DO UPDATE SET last_sent_at=excluded.last_sent_at",
            (alert_key, now),
        )
    return True


def _queue_alert(event_id, event_type, severity, endpoint, details):
    if not _alert_allowed(f"{event_type}:{endpoint}"):
        return
    threading.Thread(
        target=send_security_alert,
        args=(event_id, event_type, severity, endpoint, details),
        daemon=True,
    ).start()


def send_security_alert(event_id, event_type, severity, endpoint, details):
    payload = {
        "title": f"CareConnect security alert: {severity.upper()}",
        "event_id": event_id,
        "event_type": event_type,
        "severity": severity,
        "endpoint": endpoint or "n/a",
        "details": _safe_details(details),
        "timestamp": _utc_now(),
        "notice": "No PHI or request body is included. Review the protected security event console.",
    }
    _send_webhook(payload)
    if _emailjs_configured():
        _send_emailjs(payload)
    else:
        _send_email(payload)


def _emailjs_configured():
    return all(
        os.environ.get(name, "").strip()
        for name in ("EMAILJS_SERVICE_ID", "EMAILJS_TEMPLATE_ID", "EMAILJS_PUBLIC_KEY")
    )


def _send_emailjs(payload):
    request_data = {
        "service_id": os.environ["EMAILJS_SERVICE_ID"].strip(),
        "template_id": os.environ["EMAILJS_TEMPLATE_ID"].strip(),
        "user_id": os.environ["EMAILJS_PUBLIC_KEY"].strip(),
        "template_params": {
            "event_id": str(payload["event_id"]),
            "event_type": payload["event_type"],
            "severity": payload["severity"],
            "endpoint": payload["endpoint"],
            "timestamp": payload["timestamp"],
            "details": json.dumps(_safe_details(payload.get("details"))),
            "notice": payload["notice"],
        },
    }
    private_key = os.environ.get("EMAILJS_PRIVATE_KEY", "").strip()
    if private_key:
        request_data["accessToken"] = private_key

    request = urllib.request.Request(
        "https://api.emailjs.com/api/v1.0/email/send",
        data=json.dumps(request_data).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "CareConnect-Security-Monitor/1.0"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except urllib.error.HTTPError as exc:
        try:
            message = exc.read(500).decode("utf-8", errors="replace").replace("\n", " ").strip()
        except Exception:
            message = "response unavailable"
        print(
            f"Security alert EmailJS delivery failed: HTTP {exc.code} {message[:300]}",
            flush=True,
        )
    except Exception as exc:
        print(f"Security alert EmailJS delivery failed: {type(exc).__name__}", flush=True)


def _send_webhook(payload):
    url = os.environ.get("SECURITY_ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "CareConnect-Security-Monitor/1.0"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=8).read()
    except Exception as exc:
        print(f"Security alert webhook delivery failed: {type(exc).__name__}", flush=True)


def _send_email(payload):
    host = os.environ.get("SECURITY_SMTP_HOST", "").strip()
    recipients = [value.strip() for value in os.environ.get("SECURITY_ALERT_EMAILS", "").split(",") if value.strip()]
    sender = os.environ.get("SECURITY_ALERT_FROM", "").strip()
    if not host or not recipients or not sender:
        return

    message = EmailMessage()
    message["Subject"] = payload["title"]
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message.set_content(json.dumps(payload, indent=2))
    try:
        with smtplib.SMTP(host, int(os.environ.get("SECURITY_SMTP_PORT", "587")), timeout=10) as smtp:
            smtp.starttls()
            username = os.environ.get("SECURITY_SMTP_USERNAME", "")
            password = os.environ.get("SECURITY_SMTP_PASSWORD", "")
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    except Exception as exc:
        print(f"Security alert email delivery failed: {type(exc).__name__}", flush=True)
