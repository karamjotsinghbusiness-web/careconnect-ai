"""Encrypted, patient-owned Care Passport storage and access control.

The passport is intentionally not a global patient directory. A clinician can
open one passport only by redeeming a short-lived code created by that patient.
Clinical history is append-only: corrections are additional signed entries.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken
import segno


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "careconnect.db"
PASSPORT_VERSION = "careconnect-passport-v1"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_PATTERN = re.compile(r"^[A-HJ-NP-Z2-9]{4}-?[A-HJ-NP-Z2-9]{4}$")
PROFILE_LIST_FIELDS = {"allergies", "medications", "conditions", "care_team"}
PROFILE_FIELDS = {"preferred_name", "emergency_notes"} | PROFILE_LIST_FIELDS
ENTRY_TYPES = {
    "clinician_encounter",
    "allergy_update",
    "medication_update",
    "condition_update",
    "care_plan_update",
    "correction",
}
GRANT_DURATION_HOURS = {1, 4, 8, 12, 24}
CODE_LIFETIME_MINUTES = 15
MAX_REDEMPTION_FAILURES = 5
REDEMPTION_WINDOW_MINUTES = 10


class PassportError(ValueError):
    """Safe validation or authorization error suitable for an API response."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _db_path():
    configured = (
        os.environ.get("PATIENT_PASSPORT_DB_PATH")
        or os.environ.get("CARE_COORDINATION_DB_PATH")
        or os.environ.get("SEARCH_HISTORY_DB_PATH")
        or DEFAULT_DB_PATH
    )
    path = Path(configured)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _connect():
    connection = sqlite3.connect(_db_path(), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


@contextmanager
def _managed_connection(immediate=False):
    connection = _connect()
    try:
        if immediate:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        else:
            with connection:
                yield connection
    except Exception:
        if immediate:
            connection.rollback()
        raise
    finally:
        connection.close()


def _cipher():
    raw_key = os.environ.get("PASSPORT_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        raise PassportError(
            "Care Passport protected storage is not configured.", status=503
        )
    try:
        return Fernet(raw_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise PassportError(
            "Care Passport protected storage is not configured.", status=503
        ) from exc


def passport_available():
    try:
        _cipher()
        return True
    except PassportError:
        return False


def _integrity_key():
    raw_key = os.environ.get("PASSPORT_ENCRYPTION_KEY", "").strip().encode("ascii")
    try:
        decoded = base64.urlsafe_b64decode(raw_key)
    except Exception as exc:
        raise PassportError(
            "Care Passport protected storage is not configured.", status=503
        ) from exc
    return hashlib.sha256(decoded + b":careconnect-passport-integrity:v1").digest()


def _encrypt_json(value):
    encoded = json.dumps(
        value, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return _cipher().encrypt(encoded).decode("ascii")


def _decrypt_json(value):
    try:
        decoded = _cipher().decrypt(str(value).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PassportError(
            "Care Passport data integrity verification failed.", status=503
        ) from exc


def _digest(value):
    return hmac.new(
        _integrity_key(), str(value).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _chain_hash(*parts):
    canonical = "\x1f".join(str(part or "") for part in parts)
    return _digest(canonical)


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value, field):
    cleaned = str(value or "").strip()
    if not cleaned:
        raise PassportError(f"{field} is required.")
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PassportError(f"{field} must be a valid date and time.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_text(value, field, limit, required=False):
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise PassportError(f"{field} is required.")
    return text[:limit]


def _clean_multiline(value, field, limit, required=False):
    text = str(value or "").replace("\x00", "").strip()
    if required and not text:
        raise PassportError(f"{field} is required.")
    return text[:limit]


def _clean_list(value, field):
    if isinstance(value, str):
        values = re.split(r"\n|;", value)
    elif isinstance(value, list):
        values = value
    else:
        values = []
    cleaned = []
    for item in values:
        text = _clean_text(item, field, 240)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= 50:
            break
    return cleaned


def _default_profile():
    return {
        "preferred_name": "",
        "allergies": [],
        "medications": [],
        "conditions": [],
        "care_team": [],
        "emergency_notes": "",
    }


def initialize_patient_passport_store():
    with _connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS passport_profiles (
                patient_uid TEXT PRIMARY KEY,
                passport_id TEXT NOT NULL UNIQUE,
                encrypted_profile TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS passport_entries (
                entry_id TEXT PRIMARY KEY,
                patient_uid TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                source_type TEXT NOT NULL,
                actor_uid TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                correction_of TEXT,
                encrypted_payload TEXT NOT NULL,
                previous_hash TEXT,
                integrity_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(patient_uid) REFERENCES passport_profiles(patient_uid),
                FOREIGN KEY(correction_of) REFERENCES passport_entries(entry_id)
            );
            CREATE INDEX IF NOT EXISTS idx_passport_entries_patient_time
                ON passport_entries(patient_uid, created_at DESC);
            CREATE TRIGGER IF NOT EXISTS passport_entries_no_update
                BEFORE UPDATE ON passport_entries
                BEGIN
                    SELECT RAISE(ABORT, 'passport history is append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS passport_entries_no_delete
                BEFORE DELETE ON passport_entries
                BEGIN
                    SELECT RAISE(ABORT, 'passport history is append-only');
                END;

            CREATE TABLE IF NOT EXISTS passport_access_codes (
                code_id TEXT PRIMARY KEY,
                code_digest TEXT NOT NULL UNIQUE,
                patient_uid TEXT NOT NULL,
                grant_duration_hours INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                redeemed_at TEXT,
                redeemed_by_uid TEXT,
                grant_id TEXT,
                FOREIGN KEY(patient_uid) REFERENCES passport_profiles(patient_uid)
            );
            CREATE INDEX IF NOT EXISTS idx_passport_codes_patient_status
                ON passport_access_codes(patient_uid, status, expires_at);

            CREATE TABLE IF NOT EXISTS passport_grants (
                grant_id TEXT PRIMARY KEY,
                patient_uid TEXT NOT NULL,
                clinician_uid TEXT NOT NULL,
                clinician_role TEXT NOT NULL,
                organization_id TEXT NOT NULL,
                encrypted_grant TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                revoked_by_uid TEXT,
                FOREIGN KEY(patient_uid) REFERENCES passport_profiles(patient_uid)
            );
            CREATE INDEX IF NOT EXISTS idx_passport_grants_patient
                ON passport_grants(patient_uid, granted_at DESC);
            CREATE INDEX IF NOT EXISTS idx_passport_grants_clinician
                ON passport_grants(clinician_uid, expires_at DESC);

            CREATE TABLE IF NOT EXISTS passport_audit_log (
                event_id TEXT PRIMARY KEY,
                patient_uid TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_uid TEXT NOT NULL,
                encrypted_details TEXT NOT NULL,
                previous_hash TEXT,
                integrity_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(patient_uid) REFERENCES passport_profiles(patient_uid)
            );
            CREATE INDEX IF NOT EXISTS idx_passport_audit_patient_time
                ON passport_audit_log(patient_uid, created_at DESC);
            CREATE TRIGGER IF NOT EXISTS passport_audit_no_update
                BEFORE UPDATE ON passport_audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'passport audit history is append-only');
                END;
            CREATE TRIGGER IF NOT EXISTS passport_audit_no_delete
                BEFORE DELETE ON passport_audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'passport audit history is append-only');
                END;

            CREATE TABLE IF NOT EXISTS passport_code_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinician_uid TEXT NOT NULL,
                attempted_at TEXT NOT NULL,
                succeeded INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_passport_attempts_actor_time
                ON passport_code_attempts(clinician_uid, attempted_at DESC);
            """
        )


def _ensure_profile(connection, patient_uid):
    row = connection.execute(
        "SELECT * FROM passport_profiles WHERE patient_uid = ?", (patient_uid,)
    ).fetchone()
    if row:
        return row
    now = _iso(_utc_now())
    passport_id = "CCP-" + secrets.token_hex(8).upper()
    connection.execute(
        """
        INSERT INTO passport_profiles
            (patient_uid, passport_id, encrypted_profile, version, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        """,
        (patient_uid, passport_id, _encrypt_json(_default_profile()), now, now),
    )
    return connection.execute(
        "SELECT * FROM passport_profiles WHERE patient_uid = ?", (patient_uid,)
    ).fetchone()


def _masked_passport_id(passport_id):
    return f"•••• {str(passport_id)[-4:]}"


def _append_audit(connection, patient_uid, event_type, actor_uid, details):
    event_id = "paud_" + secrets.token_urlsafe(14)
    created_at = _iso(_utc_now())
    previous = connection.execute(
        """
        SELECT integrity_hash FROM passport_audit_log
        WHERE patient_uid = ? ORDER BY rowid DESC LIMIT 1
        """,
        (patient_uid,),
    ).fetchone()
    previous_hash = previous["integrity_hash"] if previous else ""
    encrypted = _encrypt_json(details)
    integrity_hash = _chain_hash(
        event_id, patient_uid, event_type, actor_uid, encrypted, previous_hash, created_at
    )
    connection.execute(
        """
        INSERT INTO passport_audit_log
            (event_id, patient_uid, event_type, actor_uid, encrypted_details,
             previous_hash, integrity_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            patient_uid,
            event_type,
            actor_uid,
            encrypted,
            previous_hash,
            integrity_hash,
            created_at,
        ),
    )


def _append_entry(
    connection,
    patient_uid,
    entry_type,
    source_type,
    actor_uid,
    organization_id,
    payload,
    correction_of=None,
):
    entry_id = "pent_" + secrets.token_urlsafe(14)
    created_at = _iso(_utc_now())
    previous = connection.execute(
        """
        SELECT integrity_hash FROM passport_entries
        WHERE patient_uid = ? ORDER BY rowid DESC LIMIT 1
        """,
        (patient_uid,),
    ).fetchone()
    previous_hash = previous["integrity_hash"] if previous else ""
    encrypted = _encrypt_json(payload)
    integrity_hash = _chain_hash(
        entry_id,
        patient_uid,
        entry_type,
        source_type,
        actor_uid,
        organization_id,
        correction_of or "",
        encrypted,
        previous_hash,
        created_at,
    )
    connection.execute(
        """
        INSERT INTO passport_entries
            (entry_id, patient_uid, entry_type, source_type, actor_uid,
             organization_id, correction_of, encrypted_payload, previous_hash,
             integrity_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            patient_uid,
            entry_type,
            source_type,
            actor_uid,
            organization_id,
            correction_of,
            encrypted,
            previous_hash,
            integrity_hash,
            created_at,
        ),
    )
    return entry_id


def _entry_dict(row):
    payload = _decrypt_json(row["encrypted_payload"])
    return {
        "entry_id": row["entry_id"],
        "entry_type": row["entry_type"],
        "source_type": row["source_type"],
        "correction_of": row["correction_of"],
        "created_at": row["created_at"],
        "summary": payload.get("summary", ""),
        "clinical_note": payload.get("clinical_note", ""),
        "encounter_datetime": payload.get("encounter_datetime", row["created_at"]),
        "source_of_information": payload.get("source_of_information", ""),
        "actor_display": payload.get("actor_display", ""),
        "actor_role": payload.get("actor_role", ""),
        "organization_name": payload.get("organization_name", ""),
        "changed_sections": payload.get("changed_sections", []),
        "append_only": True,
    }


def _grant_dict(row):
    details = _decrypt_json(row["encrypted_grant"])
    return {
        "grant_id": row["grant_id"],
        "clinician_role": row["clinician_role"],
        "organization_id": row["organization_id"],
        "clinician_display": details.get("clinician_display", "Verified clinician"),
        "organization_name": details.get("organization_name", "Verified organization"),
        "granted_at": row["granted_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "active": row["revoked_at"] is None and row["expires_at"] > _iso(_utc_now()),
    }


def _audit_dict(row):
    details = _decrypt_json(row["encrypted_details"])
    return {
        "event_id": row["event_id"],
        "event_type": row["event_type"],
        "created_at": row["created_at"],
        "actor_display": details.get("actor_display", "CareConnect user"),
        "actor_role": details.get("actor_role", ""),
        "organization_name": details.get("organization_name", "CareConnect AI"),
        "action": details.get("action", event_label(row["event_type"])),
    }


def event_label(event_type):
    return {
        "passport_created": "Created Care Passport",
        "profile_updated": "Updated health summary",
        "access_code_created": "Created temporary access code",
        "access_code_cancelled": "Cancelled temporary access code",
        "access_granted": "Granted temporary clinician access",
        "passport_viewed": "Viewed health summary",
        "entry_added": "Added signed history entry",
        "grant_revoked": "Ended clinician access",
    }.get(event_type, str(event_type).replace("_", " ").capitalize())


def _verify_entry_chain(connection, patient_uid):
    previous_hash = ""
    rows = connection.execute(
        """
        SELECT * FROM passport_entries
        WHERE patient_uid = ?
        ORDER BY rowid ASC
        """,
        (patient_uid,),
    ).fetchall()
    for row in rows:
        expected = _chain_hash(
            row["entry_id"],
            row["patient_uid"],
            row["entry_type"],
            row["source_type"],
            row["actor_uid"],
            row["organization_id"],
            row["correction_of"] or "",
            row["encrypted_payload"],
            previous_hash,
            row["created_at"],
        )
        if (
            not hmac.compare_digest(row["previous_hash"] or "", previous_hash)
            or not hmac.compare_digest(row["integrity_hash"], expected)
        ):
            raise PassportError(
                "Care Passport data integrity verification failed.", status=503
            )
        previous_hash = row["integrity_hash"]


def _verify_audit_chain(connection, patient_uid):
    previous_hash = ""
    rows = connection.execute(
        """
        SELECT * FROM passport_audit_log
        WHERE patient_uid = ?
        ORDER BY rowid ASC
        """,
        (patient_uid,),
    ).fetchall()
    for row in rows:
        expected = _chain_hash(
            row["event_id"],
            row["patient_uid"],
            row["event_type"],
            row["actor_uid"],
            row["encrypted_details"],
            previous_hash,
            row["created_at"],
        )
        if (
            not hmac.compare_digest(row["previous_hash"] or "", previous_hash)
            or not hmac.compare_digest(row["integrity_hash"], expected)
        ):
            raise PassportError(
                "Care Passport data integrity verification failed.", status=503
            )
        previous_hash = row["integrity_hash"]


def _load_patient_bundle(connection, patient_uid, include_audit=True):
    profile_row = _ensure_profile(connection, patient_uid)
    _verify_entry_chain(connection, patient_uid)
    if include_audit:
        _verify_audit_chain(connection, patient_uid)
    profile = _decrypt_json(profile_row["encrypted_profile"])
    entries = [
        _entry_dict(row)
        for row in connection.execute(
            """
            SELECT * FROM passport_entries
            WHERE patient_uid = ?
            ORDER BY created_at DESC, entry_id DESC LIMIT 200
            """,
            (patient_uid,),
        ).fetchall()
    ]
    grants = [
        _grant_dict(row)
        for row in connection.execute(
            """
            SELECT * FROM passport_grants
            WHERE patient_uid = ?
            ORDER BY granted_at DESC LIMIT 100
            """,
            (patient_uid,),
        ).fetchall()
    ]
    active_codes = [
        {
            "code_id": row["code_id"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "grant_duration_hours": row["grant_duration_hours"],
            "status": row["status"],
        }
        for row in connection.execute(
            """
            SELECT * FROM passport_access_codes
            WHERE patient_uid = ? AND status = 'active' AND expires_at > ?
            ORDER BY created_at DESC
            """,
            (patient_uid, _iso(_utc_now())),
        ).fetchall()
    ]
    audit = []
    if include_audit:
        audit = [
            _audit_dict(row)
            for row in connection.execute(
                """
                SELECT * FROM passport_audit_log
                WHERE patient_uid = ?
                ORDER BY created_at DESC, event_id DESC LIMIT 200
                """,
                (patient_uid,),
            ).fetchall()
        ]
    return {
        "passport_version": PASSPORT_VERSION,
        "passport_id_masked": _masked_passport_id(profile_row["passport_id"]),
        "profile": profile,
        "entries": entries,
        "grants": grants,
        "active_codes": active_codes,
        "audit": audit,
        "updated_at": profile_row["updated_at"],
        "append_only_history": True,
    }


def get_patient_passport(patient_uid):
    with _managed_connection() as connection:
        created = connection.execute(
            "SELECT 1 FROM passport_profiles WHERE patient_uid = ?", (patient_uid,)
        ).fetchone() is None
        bundle = _load_patient_bundle(connection, patient_uid)
        if created:
            _append_audit(
                connection,
                patient_uid,
                "passport_created",
                patient_uid,
                {
                    "actor_display": "Patient",
                    "actor_role": "patient",
                    "organization_name": "CareConnect AI",
                    "action": "Created Care Passport",
                },
            )
            bundle = _load_patient_bundle(connection, patient_uid)
        return bundle


def save_patient_profile(patient_uid, payload):
    if not isinstance(payload, dict):
        raise PassportError("A valid Care Passport profile is required.")
    cleaned = {
        "preferred_name": _clean_text(
            payload.get("preferred_name"), "Preferred name", 120
        ),
        "emergency_notes": _clean_multiline(
            payload.get("emergency_notes"), "Emergency notes", 1000
        ),
    }
    for field in PROFILE_LIST_FIELDS:
        cleaned[field] = _clean_list(payload.get(field), field.replace("_", " "))

    with _managed_connection(immediate=True) as connection:
        profile_row = _ensure_profile(connection, patient_uid)
        current = _decrypt_json(profile_row["encrypted_profile"])
        changed = [field for field in PROFILE_FIELDS if current.get(field) != cleaned.get(field)]
        if changed:
            now = _iso(_utc_now())
            connection.execute(
                """
                UPDATE passport_profiles
                SET encrypted_profile = ?, version = version + 1, updated_at = ?
                WHERE patient_uid = ?
                """,
                (_encrypt_json(cleaned), now, patient_uid),
            )
            actor_display = cleaned.get("preferred_name") or "Patient"
            _append_entry(
                connection,
                patient_uid,
                "profile_update",
                "patient_reported",
                patient_uid,
                "self_reported",
                {
                    "summary": "Patient updated " + ", ".join(
                        field.replace("_", " ") for field in changed
                    ),
                    "clinical_note": "",
                    "encounter_datetime": now,
                    "source_of_information": "Patient-entered Care Passport profile",
                    "actor_display": actor_display,
                    "actor_role": "patient",
                    "organization_name": "Self-reported",
                    "changed_sections": changed,
                },
            )
            _append_audit(
                connection,
                patient_uid,
                "profile_updated",
                patient_uid,
                {
                    "actor_display": actor_display,
                    "actor_role": "patient",
                    "organization_name": "CareConnect AI",
                    "action": "Updated health summary",
                    "changed_sections": changed,
                },
            )
        return _load_patient_bundle(connection, patient_uid)


def _new_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(value):
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:]}"
    return str(value or "").strip().upper()


def create_access_code(patient_uid, grant_duration_hours=4):
    try:
        duration = int(grant_duration_hours)
    except (TypeError, ValueError) as exc:
        raise PassportError("Choose a valid access duration.") from exc
    if duration not in GRANT_DURATION_HOURS:
        raise PassportError("Choose a valid access duration.")

    now = _utc_now()
    expires_at = now + timedelta(minutes=CODE_LIFETIME_MINUTES)
    with _managed_connection(immediate=True) as connection:
        _ensure_profile(connection, patient_uid)
        connection.execute(
            """
            UPDATE passport_access_codes
            SET status = 'cancelled'
            WHERE patient_uid = ? AND status = 'active'
            """,
            (patient_uid,),
        )
        for _ in range(8):
            code = _new_code()
            digest = _digest(normalize_code(code))
            if not connection.execute(
                "SELECT 1 FROM passport_access_codes WHERE code_digest = ?", (digest,)
            ).fetchone():
                break
        else:
            raise PassportError("Could not create a temporary access code. Try again.")
        code_id = "pcode_" + secrets.token_urlsafe(14)
        connection.execute(
            """
            INSERT INTO passport_access_codes
                (code_id, code_digest, patient_uid, grant_duration_hours, status,
                 created_at, expires_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (code_id, digest, patient_uid, duration, _iso(now), _iso(expires_at)),
        )
        _append_audit(
            connection,
            patient_uid,
            "access_code_created",
            patient_uid,
            {
                "actor_display": "Patient",
                "actor_role": "patient",
                "organization_name": "CareConnect AI",
                "action": f"Created {duration}-hour temporary access",
            },
        )

    clinician_url = os.environ.get(
        "CLINICIAN_PASSPORT_URL",
        "https://careconnect-doctors-19ace.web.app/passport.html",
    ).split("?", 1)[0] + "?" + urlencode({"passport_code": code})
    qr_data_uri = segno.make(clinician_url, micro=False).svg_data_uri(
        scale=4, border=2, dark="#081B36", light="#FFFFFF"
    )
    return {
        "code_id": code_id,
        "code": code,
        "expires_at": _iso(expires_at),
        "grant_duration_hours": duration,
        "qr_data_uri": qr_data_uri,
        "qr_url": clinician_url,
    }


def cancel_access_code(patient_uid, code_id):
    with _managed_connection(immediate=True) as connection:
        row = connection.execute(
            """
            SELECT * FROM passport_access_codes
            WHERE code_id = ? AND patient_uid = ?
            """,
            (code_id, patient_uid),
        ).fetchone()
        if not row:
            raise PassportError("Temporary access code not found.", status=404)
        if row["status"] == "active":
            connection.execute(
                "UPDATE passport_access_codes SET status = 'cancelled' WHERE code_id = ?",
                (code_id,),
            )
            _append_audit(
                connection,
                patient_uid,
                "access_code_cancelled",
                patient_uid,
                {
                    "actor_display": "Patient",
                    "actor_role": "patient",
                    "organization_name": "CareConnect AI",
                    "action": "Cancelled temporary access code",
                },
            )
    return {"code_id": code_id, "status": "cancelled"}


def _redemption_limit(connection, clinician_uid):
    cutoff = _iso(_utc_now() - timedelta(minutes=REDEMPTION_WINDOW_MINUTES))
    failures = connection.execute(
        """
        SELECT COUNT(*) FROM passport_code_attempts
        WHERE clinician_uid = ? AND attempted_at >= ? AND succeeded = 0
        """,
        (clinician_uid, cutoff),
    ).fetchone()[0]
    if failures >= MAX_REDEMPTION_FAILURES:
        raise PassportError(
            "Too many invalid code attempts. Wait before trying again.", status=429
        )


def redeem_access_code(
    clinician_uid,
    clinician_role,
    clinician_display,
    organization_id,
    organization_name,
    code,
):
    normalized = normalize_code(code)
    if not CODE_PATTERN.fullmatch(normalized):
        raise PassportError("The temporary access code is invalid or expired.", status=403)

    now = _utc_now()
    invalid = False
    grant_result = None
    with _managed_connection(immediate=True) as connection:
        _redemption_limit(connection, clinician_uid)
        row = connection.execute(
            "SELECT * FROM passport_access_codes WHERE code_digest = ?",
            (_digest(normalized),),
        ).fetchone()
        valid = (
            row
            and row["status"] == "active"
            and row["expires_at"] > _iso(now)
        )
        connection.execute(
            """
            INSERT INTO passport_code_attempts
                (clinician_uid, attempted_at, succeeded)
            VALUES (?, ?, ?)
            """,
            (clinician_uid, _iso(now), 1 if valid else 0),
        )
        if not valid:
            invalid = True
        else:
            grant_id = "pgrant_" + secrets.token_urlsafe(14)
            expires_at = now + timedelta(hours=row["grant_duration_hours"])
            grant_details = {
                "clinician_display": _clean_text(
                    clinician_display, "Clinician name", 120
                ) or "Verified clinician",
                "organization_name": _clean_text(
                    organization_name, "Organization", 160
                ) or "Verified organization",
            }
            updated = connection.execute(
                """
                UPDATE passport_access_codes
                SET status = 'redeemed', redeemed_at = ?, redeemed_by_uid = ?, grant_id = ?
                WHERE code_id = ? AND status = 'active'
                """,
                (_iso(now), clinician_uid, grant_id, row["code_id"]),
            )
            if updated.rowcount != 1:
                raise PassportError(
                    "The temporary access code was already used.", status=409
                )
            connection.execute(
                """
                INSERT INTO passport_grants
                    (grant_id, patient_uid, clinician_uid, clinician_role,
                     organization_id, encrypted_grant, granted_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    grant_id,
                    row["patient_uid"],
                    clinician_uid,
                    clinician_role,
                    organization_id,
                    _encrypt_json(grant_details),
                    _iso(now),
                    _iso(expires_at),
                ),
            )
            _append_audit(
                connection,
                row["patient_uid"],
                "access_granted",
                clinician_uid,
                {
                    **grant_details,
                    "actor_display": grant_details["clinician_display"],
                    "actor_role": clinician_role,
                    "action": f"Access granted for {row['grant_duration_hours']} hours",
                },
            )
            grant_result = {
                "grant_id": grant_id,
                "expires_at": _iso(expires_at),
                "organization_name": grant_details["organization_name"],
            }
    if invalid:
        raise PassportError(
            "The temporary access code is invalid or expired.", status=403
        )
    return grant_result


def _active_grant(connection, grant_id, clinician_uid, organization_id=None):
    row = connection.execute(
        """
        SELECT * FROM passport_grants
        WHERE grant_id = ? AND clinician_uid = ?
        """,
        (grant_id, clinician_uid),
    ).fetchone()
    if not row:
        raise PassportError("Patient-approved passport access was not found.", status=404)
    if organization_id and row["organization_id"] != organization_id:
        raise PassportError(
            "This access grant belongs to a different verified healthcare organization.",
            status=403,
        )
    if row["revoked_at"] is not None or row["expires_at"] <= _iso(_utc_now()):
        raise PassportError(
            "Patient-approved passport access has ended.", status=403
        )
    return row


def get_clinician_passport(grant_id, clinician_uid, actor_details, record_view=True):
    with _managed_connection(immediate=record_view) as connection:
        grant_row = _active_grant(
            connection,
            grant_id,
            clinician_uid,
            actor_details["organization_id"],
        )
        if record_view:
            _append_audit(
                connection,
                grant_row["patient_uid"],
                "passport_viewed",
                clinician_uid,
                {
                    "clinician_display": actor_details["clinician_display"],
                    "actor_display": actor_details["clinician_display"],
                    "actor_role": actor_details["clinician_role"],
                    "organization_name": actor_details["organization_name"],
                    "action": "Viewed health summary and history",
                },
            )
        bundle = _load_patient_bundle(
            connection, grant_row["patient_uid"], include_audit=False
        )
        return {
            "passport_version": bundle["passport_version"],
            "passport_id_masked": bundle["passport_id_masked"],
            "profile": bundle["profile"],
            "entries": bundle["entries"],
            "grant": _grant_dict(grant_row),
            "append_only_history": True,
        }


def add_clinician_entry(
    grant_id,
    clinician_uid,
    actor_details,
    payload,
):
    if not isinstance(payload, dict):
        raise PassportError("A valid signed history entry is required.")
    entry_type = str(payload.get("entry_type") or "").strip().lower()
    if entry_type not in ENTRY_TYPES:
        raise PassportError("Choose a valid entry type.")
    encounter = _parse_iso(payload.get("encounter_datetime"), "Encounter date and time")
    if encounter > _utc_now() + timedelta(hours=24):
        raise PassportError("Encounter date and time cannot be in the distant future.")
    summary = _clean_text(payload.get("summary"), "Summary", 240, required=True)
    clinical_note = _clean_multiline(
        payload.get("clinical_note"), "Clinical note", 4000, required=True
    )
    source = _clean_text(
        payload.get("source_of_information"),
        "Source of information",
        160,
        required=True,
    )
    if payload.get("append_only_confirmed") is not True:
        raise PassportError(
            "Confirm that this signed entry becomes part of the append-only history."
        )
    correction_of = _clean_text(
        payload.get("correction_of"), "Corrected entry", 80
    )
    if entry_type == "correction" and not correction_of:
        raise PassportError("Choose the earlier entry this correction addresses.")
    if entry_type != "correction":
        correction_of = None

    with _managed_connection(immediate=True) as connection:
        grant_row = _active_grant(
            connection,
            grant_id,
            clinician_uid,
            actor_details["organization_id"],
        )
        if correction_of and not connection.execute(
            """
            SELECT 1 FROM passport_entries
            WHERE entry_id = ? AND patient_uid = ?
            """,
            (correction_of, grant_row["patient_uid"]),
        ).fetchone():
            raise PassportError("The corrected history entry was not found.", status=404)
        entry_id = _append_entry(
            connection,
            grant_row["patient_uid"],
            entry_type,
            "clinician_confirmed",
            clinician_uid,
            actor_details["organization_id"],
            {
                "summary": summary,
                "clinical_note": clinical_note,
                "encounter_datetime": _iso(encounter),
                "source_of_information": source,
                "actor_display": actor_details["clinician_display"],
                "actor_role": actor_details["clinician_role"],
                "organization_name": actor_details["organization_name"],
                "changed_sections": [],
            },
            correction_of=correction_of,
        )
        _append_audit(
            connection,
            grant_row["patient_uid"],
            "entry_added",
            clinician_uid,
            {
                "actor_display": actor_details["clinician_display"],
                "actor_role": actor_details["clinician_role"],
                "organization_name": actor_details["organization_name"],
                "action": "Added signed append-only history entry",
            },
        )
        row = connection.execute(
            "SELECT * FROM passport_entries WHERE entry_id = ?", (entry_id,)
        ).fetchone()
        return _entry_dict(row)


def revoke_patient_grant(patient_uid, grant_id):
    now = _iso(_utc_now())
    with _managed_connection(immediate=True) as connection:
        row = connection.execute(
            """
            SELECT * FROM passport_grants
            WHERE grant_id = ? AND patient_uid = ?
            """,
            (grant_id, patient_uid),
        ).fetchone()
        if not row:
            raise PassportError("Clinician access grant was not found.", status=404)
        if row["revoked_at"] is None:
            connection.execute(
                """
                UPDATE passport_grants
                SET revoked_at = ?, revoked_by_uid = ?
                WHERE grant_id = ?
                """,
                (now, patient_uid, grant_id),
            )
            _append_audit(
                connection,
                patient_uid,
                "grant_revoked",
                patient_uid,
                {
                    "actor_display": "Patient",
                    "actor_role": "patient",
                    "organization_name": "CareConnect AI",
                    "action": "Revoked clinician access",
                },
            )
    return {"grant_id": grant_id, "revoked_at": now}


def end_clinician_grant(grant_id, clinician_uid, actor_details):
    now = _iso(_utc_now())
    with _managed_connection(immediate=True) as connection:
        row = connection.execute(
            """
            SELECT * FROM passport_grants
            WHERE grant_id = ? AND clinician_uid = ?
            """,
            (grant_id, clinician_uid),
        ).fetchone()
        if not row:
            raise PassportError("Patient-approved passport access was not found.", status=404)
        if row["organization_id"] != actor_details["organization_id"]:
            raise PassportError(
                "This access grant belongs to a different verified healthcare organization.",
                status=403,
            )
        if row["revoked_at"] is None:
            connection.execute(
                """
                UPDATE passport_grants
                SET revoked_at = ?, revoked_by_uid = ?
                WHERE grant_id = ?
                """,
                (now, clinician_uid, grant_id),
            )
            _append_audit(
                connection,
                row["patient_uid"],
                "grant_revoked",
                clinician_uid,
                {
                    "actor_display": actor_details["clinician_display"],
                    "actor_role": actor_details["clinician_role"],
                    "organization_name": actor_details["organization_name"],
                    "action": "Clinician ended temporary access",
                },
            )
    return {"grant_id": grant_id, "revoked_at": now}
