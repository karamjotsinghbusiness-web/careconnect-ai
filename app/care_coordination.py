"""Consent-gated care coordination and prior-authorization request tracking.

This module records administrative requests and their verified workflow events.
It intentionally does not contact providers, submit payer transactions, or claim
that an appointment, referral, benefit, or authorization has been confirmed.
Those statuses can only be added by an authenticated workforce user or a future
verified partner integration.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "database" / "careconnect.db"
CONSENT_VERSION = "careconnect-coordination-v1"
REQUEST_TYPES = {"appointment_referral", "prior_authorization"}
CONTACT_PREFERENCES = {"in_app", "email", "phone"}
DESTINATION_MODES = {"specific_provider", "find_for_me"}
APPOINTMENT_TIMING = {
    "first_available",
    "within_two_weeks",
    "specific_date",
    "no_preference",
}
REFERRAL_STATUSES = {"not_sure", "have_referral", "need_referral"}
AUTH_REQUIREMENT_ANSWERS = {"unknown", "yes", "no"}
PERMISSIONS = {
    "contact_selected_providers",
    "request_appointment",
    "share_referral_summary",
    "contact_insurer",
    "contact_ordering_provider",
    "share_authorization_packet",
}
PATIENT_CANCELLABLE_STATUSES = {
    "received",
    "assessment_ready",
    "needs_information",
    "ready_for_review",
    "sent_to_destination",
    "acknowledged",
    "accepted",
}
WORKFORCE_TRANSITIONS = {
    "received": {"ready_for_review", "needs_information", "cancelled"},
    "assessment_ready": {"ready_for_review", "needs_information", "cancelled"},
    "needs_information": {"ready_for_review", "cancelled"},
    "ready_for_review": {"sent_to_destination", "needs_information", "cancelled"},
    "sent_to_destination": {"acknowledged", "blocked", "cancelled"},
    "acknowledged": {"accepted", "scheduled", "blocked", "cancelled"},
    "accepted": {"scheduled", "blocked", "cancelled"},
    "scheduled": {"completed", "blocked"},
    "blocked": {"ready_for_review", "cancelled"},
}
EXTERNALLY_SENT_STATUSES = {
    "sent_to_destination",
    "acknowledged",
    "accepted",
    "scheduled",
    "completed",
    "blocked",
}
CLIENT_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{12,80}$")


class CareCoordinationError(ValueError):
    """Safe validation or workflow error suitable for an API response."""


def _db_path():
    configured = (
        os.environ.get("CARE_COORDINATION_DB_PATH")
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
def _managed_connection():
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _utc_now():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.isoformat().replace("+00:00", "Z")


def _clean_text(value, field, limit, required=False):
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise CareCoordinationError(f"{field} is required.")
    return text[:limit]


def _clean_choice(value, field, choices, default=None):
    cleaned = str(value or default or "").strip().lower()
    if cleaned not in choices:
        raise CareCoordinationError(f"Choose a valid {field}.")
    return cleaned


def _clean_date(value, field):
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    try:
        datetime.strptime(cleaned, "%Y-%m-%d")
    except ValueError as exc:
        raise CareCoordinationError(f"{field} must be a valid date.") from exc
    return cleaned


def _clean_phone(value):
    phone = _clean_text(value, "Provider phone", 40)
    if phone and len(re.sub(r"\D", "", phone)) < 7:
        raise CareCoordinationError("Enter a valid provider phone number or leave it blank.")
    return phone


def _permissions(payload):
    values = payload.get("consent_permissions")
    if not isinstance(values, list):
        raise CareCoordinationError("Select the actions you authorize CareConnect to take.")
    cleaned = []
    for value in values:
        permission = str(value or "").strip().lower()
        if permission in PERMISSIONS and permission not in cleaned:
            cleaned.append(permission)
    return cleaned


def _validate_common(payload, allow_real_phi):
    if not isinstance(payload, dict):
        raise CareCoordinationError("A valid request is required.")

    demo_only = payload.get("demo_only_confirmed") is True
    if not allow_real_phi and not demo_only:
        raise CareCoordinationError(
            "Real patient information is disabled. Use fictional demonstration data only."
        )

    client_request_id = _clean_text(
        payload.get("client_request_id"), "Request identifier", 80, required=True
    )
    if not CLIENT_REQUEST_ID_PATTERN.fullmatch(client_request_id):
        raise CareCoordinationError("The request identifier is invalid. Refresh and try again.")

    if payload.get("patient_authorization_confirmed") is not True:
        raise CareCoordinationError(
            "Patient authorization is required before CareConnect can create this request."
        )
    if payload.get("data_review_confirmed") is not True:
        raise CareCoordinationError(
            "Confirm that the displayed information and recipients were reviewed."
        )

    request_type = _clean_choice(
        payload.get("request_type"), "request type", REQUEST_TYPES
    )
    contact_preference = _clean_choice(
        payload.get("contact_preference"),
        "contact preference",
        CONTACT_PREFERENCES,
        default="in_app",
    )
    permissions = _permissions(payload)

    return {
        "client_request_id": client_request_id,
        "request_type": request_type,
        "contact_preference": contact_preference,
        "demo_only_confirmed": demo_only,
        "permissions": permissions,
    }


def _validate_appointment(payload, common):
    required_permissions = {"contact_selected_providers", "request_appointment"}
    if not required_permissions.issubset(common["permissions"]):
        raise CareCoordinationError(
            "Permission to contact providers and request an appointment is required."
        )

    destination_mode = _clean_choice(
        payload.get("destination_mode"),
        "provider selection",
        DESTINATION_MODES,
        default="specific_provider",
    )
    provider_name = _clean_text(payload.get("provider_name"), "Provider or clinic", 160)
    if destination_mode == "specific_provider" and not provider_name:
        raise CareCoordinationError("Enter the provider or clinic CareConnect may contact.")

    timing = _clean_choice(
        payload.get("appointment_timing"),
        "appointment timing",
        APPOINTMENT_TIMING,
        default="first_available",
    )
    requested_date = _clean_date(payload.get("requested_date"), "Requested date")
    if timing == "specific_date" and not requested_date:
        raise CareCoordinationError("Choose the date you want CareConnect to request.")

    return {
        "destination_mode": destination_mode,
        "provider_name": provider_name,
        "provider_phone": _clean_phone(payload.get("provider_phone")),
        "provider_city": _clean_text(payload.get("provider_city"), "Provider city", 100),
        "service_needed": _clean_text(
            payload.get("service_needed"), "Service or appointment needed", 180, required=True
        ),
        "appointment_timing": timing,
        "requested_date": requested_date,
        "referral_status": _clean_choice(
            payload.get("referral_status"),
            "referral status",
            REFERRAL_STATUSES,
            default="not_sure",
        ),
        "notes": _clean_text(payload.get("notes"), "Coordination notes", 600),
        "contact_preference": common["contact_preference"],
        "consent_permissions": common["permissions"],
    }


def _build_prior_authorization_assessment(details):
    missing = []
    if not details["member_id_available"]:
        missing.append("Insurance card or member ID must be available to authorized staff.")
    if not details["date_of_birth_available"]:
        missing.append("Patient date of birth must be available to authorized staff.")
    if not details["ordering_provider"]:
        missing.append("Ordering or referring provider")
    if not details["provider_order_available"]:
        missing.append("Provider order, referral, or prescription, if the payer requires one")
    if not details["clinical_notes_available"]:
        missing.append("Supporting clinical notes or test results, if required by the payer")

    if details["known_requirement"] == "yes":
        requirement_label = "Patient reports that prior authorization is required"
    elif details["known_requirement"] == "no":
        requirement_label = "Patient reports that prior authorization may not be required"
    else:
        requirement_label = "Prior-authorization requirement has not been verified"

    ready = not missing
    return {
        "verification_status": "not_verified",
        "verification_status_label": "Requirement and coverage not verified",
        "requirement_reported": details["known_requirement"],
        "requirement_label": requirement_label,
        "readiness": "ready_for_staff_review" if ready else "needs_information",
        "readiness_label": (
            "Ready for staff review — not submitted"
            if ready
            else "More information may be needed before submission"
        ),
        "missing_items": missing,
        "checklist": [
            {
                "id": "verify_requirement",
                "label": "Verify whether this exact service requires prior authorization",
                "status": "not_started",
            },
            {
                "id": "verify_network",
                "label": "Verify the individual clinician and facility network status",
                "status": "not_started",
            },
            {
                "id": "collect_requirements",
                "label": "Obtain payer-specific documentation and coding requirements",
                "status": "not_started",
            },
            {
                "id": "submit_request",
                "label": "Submit through an approved payer or clearinghouse channel",
                "status": "not_started",
            },
            {
                "id": "record_decision",
                "label": "Record the payer reference number, decision, dates, and reason",
                "status": "not_started",
            },
        ],
        "next_steps": [
            "An authorized staff member must verify requirements with the payer.",
            "Do not cancel or delay care solely because this readiness check is incomplete.",
            "A payer response—not CareConnect—determines whether authorization is approved.",
        ],
        "disclaimer": (
            "CareConnect has not contacted the payer and has not verified benefits, "
            "medical necessity, network participation, or authorization."
        ),
    }


def _validate_prior_authorization(payload, common):
    if "contact_insurer" not in common["permissions"]:
        raise CareCoordinationError(
            "Permission to contact the insurer is required for authorization assistance."
        )

    details = {
        "payer": _clean_text(payload.get("payer"), "Insurance company", 120, required=True),
        "plan_type": _clean_text(payload.get("plan_type"), "Plan type", 80),
        "service_or_item": _clean_text(
            payload.get("service_or_item"),
            "Service, procedure, medication, or equipment",
            200,
            required=True,
        ),
        "known_requirement": _clean_choice(
            payload.get("known_requirement"),
            "prior-authorization answer",
            AUTH_REQUIREMENT_ANSWERS,
            default="unknown",
        ),
        "ordering_provider": _clean_text(
            payload.get("ordering_provider"), "Ordering provider", 160
        ),
        "servicing_provider": _clean_text(
            payload.get("servicing_provider"), "Servicing provider", 160
        ),
        "scheduled_date": _clean_date(payload.get("scheduled_date"), "Scheduled date"),
        "member_id_available": payload.get("member_id_available") is True,
        "date_of_birth_available": payload.get("date_of_birth_available") is True,
        "provider_order_available": payload.get("provider_order_available") is True,
        "clinical_notes_available": payload.get("clinical_notes_available") is True,
        "notes": _clean_text(payload.get("notes"), "Authorization notes", 600),
        "contact_preference": common["contact_preference"],
        "consent_permissions": common["permissions"],
    }
    return details, _build_prior_authorization_assessment(details)


def initialize_care_coordination_store():
    with _managed_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS care_requests (
                request_id TEXT PRIMARY KEY,
                client_request_id TEXT NOT NULL,
                owner_uid TEXT NOT NULL,
                request_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL,
                demo_only INTEGER NOT NULL,
                consent_version TEXT NOT NULL,
                consent_granted_at TEXT NOT NULL,
                consent_expires_at TEXT NOT NULL,
                consent_revoked_at TEXT,
                external_delivery_status TEXT NOT NULL,
                last_verified_event TEXT NOT NULL,
                payload TEXT NOT NULL,
                assessment TEXT NOT NULL,
                UNIQUE(owner_uid, client_request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_care_requests_owner_created
                ON care_requests(owner_uid, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_care_requests_status_created
                ON care_requests(status, created_at);
            CREATE TABLE IF NOT EXISTS care_request_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_uid TEXT,
                event_type TEXT NOT NULL,
                from_status TEXT,
                to_status TEXT NOT NULL,
                FOREIGN KEY(request_id) REFERENCES care_requests(request_id)
            );
            CREATE INDEX IF NOT EXISTS idx_care_request_events_request
                ON care_request_events(request_id, id);
            """
        )


def _row_to_request(connection, row):
    if row is None:
        return None
    item = dict(row)
    item["demo_only"] = bool(item["demo_only"])
    item["payload"] = json.loads(item["payload"])
    item["assessment"] = json.loads(item["assessment"])
    events = connection.execute(
        """
        SELECT created_at, actor_type, event_type, from_status, to_status
        FROM care_request_events
        WHERE request_id = ?
        ORDER BY id ASC
        """,
        (item["request_id"],),
    ).fetchall()
    item["events"] = [dict(event) for event in events]
    return item


def _insert_event(
    connection,
    request_id,
    actor_type,
    actor_uid,
    event_type,
    from_status,
    to_status,
    created_at,
):
    connection.execute(
        """
        INSERT INTO care_request_events
            (request_id, created_at, actor_type, actor_uid, event_type, from_status, to_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            created_at,
            actor_type,
            actor_uid,
            event_type,
            from_status,
            to_status,
        ),
    )


def create_care_request(owner_uid, payload, allow_real_phi=False):
    owner_uid = _clean_text(owner_uid, "Authenticated user", 160, required=True)
    common = _validate_common(payload, allow_real_phi=allow_real_phi)

    if common["request_type"] == "appointment_referral":
        details = _validate_appointment(payload, common)
        assessment = {
            "readiness": "ready_for_staff_review",
            "readiness_label": "Ready for staff review — not sent",
            "next_steps": [
                "CareConnect must review the request before contacting any destination.",
                "The patient must approve a different destination if the selected option cannot help.",
                "An appointment is not scheduled until the destination confirms it.",
            ],
            "disclaimer": (
                "Creating this request does not send a referral or schedule an appointment."
            ),
        }
        initial_status = "received"
    else:
        details, assessment = _validate_prior_authorization(payload, common)
        initial_status = "assessment_ready"

    now = _utc_now()
    created_at = _iso(now)
    expires_at = _iso(now + timedelta(days=30))
    request_id = f"CCR-{now.strftime('%Y%m%d')}-{secrets.token_hex(4).upper()}"
    consent = {
        "version": CONSENT_VERSION,
        "permissions": common["permissions"],
        "granted_at": created_at,
        "expires_at": expires_at,
        "revoked_at": None,
        "patient_authorization_confirmed": True,
        "data_review_confirmed": True,
        "statement": (
            "The patient authorized CareConnect to perform only the listed administrative "
            "actions and share only the information required for those actions."
        ),
    }
    stored_payload = {
        "request_type": common["request_type"],
        "contact_preference": common["contact_preference"],
        "details": details,
        "consent": consent,
    }

    initialize_care_coordination_store()
    with _managed_connection() as connection:
        existing = connection.execute(
            """
            SELECT * FROM care_requests
            WHERE owner_uid = ? AND client_request_id = ?
            """,
            (owner_uid, common["client_request_id"]),
        ).fetchone()
        if existing is not None:
            return _row_to_request(connection, existing), False

        connection.execute(
            """
            INSERT INTO care_requests (
                request_id, client_request_id, owner_uid, request_type,
                created_at, updated_at, status, demo_only, consent_version,
                consent_granted_at, consent_expires_at, consent_revoked_at,
                external_delivery_status, last_verified_event, payload, assessment
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                request_id,
                common["client_request_id"],
                owner_uid,
                common["request_type"],
                created_at,
                created_at,
                initial_status,
                int(common["demo_only_confirmed"]),
                CONSENT_VERSION,
                created_at,
                expires_at,
                "not_sent",
                "Patient authorization recorded; external delivery not verified.",
                json.dumps(stored_payload, allow_nan=False),
                json.dumps(assessment, allow_nan=False),
            ),
        )
        _insert_event(
            connection,
            request_id,
            "patient",
            owner_uid,
            "request_created",
            None,
            initial_status,
            created_at,
        )
        row = connection.execute(
            "SELECT * FROM care_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return _row_to_request(connection, row), True


def list_patient_requests(owner_uid, limit=50):
    owner_uid = _clean_text(owner_uid, "Authenticated user", 160, required=True)
    limit = max(1, min(int(limit), 100))
    initialize_care_coordination_store()
    with _managed_connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM care_requests
            WHERE owner_uid = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (owner_uid, limit),
        ).fetchall()
        return [_row_to_request(connection, row) for row in rows]


def list_work_queue(include_demo=True, limit=100):
    limit = max(1, min(int(limit), 200))
    initialize_care_coordination_store()
    query = "SELECT * FROM care_requests"
    params = []
    if not include_demo:
        query += " WHERE demo_only = 0"
    query += " ORDER BY created_at ASC LIMIT ?"
    params.append(limit)
    with _managed_connection() as connection:
        rows = connection.execute(query, params).fetchall()
        return [_row_to_request(connection, row) for row in rows]


def revoke_patient_request(owner_uid, request_id):
    owner_uid = _clean_text(owner_uid, "Authenticated user", 160, required=True)
    request_id = _clean_text(request_id, "Request identifier", 80, required=True)
    initialize_care_coordination_store()
    with _managed_connection() as connection:
        row = connection.execute(
            """
            SELECT * FROM care_requests
            WHERE request_id = ? AND owner_uid = ?
            """,
            (request_id, owner_uid),
        ).fetchone()
        if row is None:
            raise CareCoordinationError("Care request not found.")
        if row["consent_revoked_at"]:
            return _row_to_request(connection, row), False
        if row["status"] not in PATIENT_CANCELLABLE_STATUSES:
            raise CareCoordinationError(
                "This request can no longer be cancelled from the patient portal."
            )

        now = _iso(_utc_now())
        old_status = row["status"]
        connection.execute(
            """
            UPDATE care_requests
            SET status = 'revoked', updated_at = ?, consent_revoked_at = ?,
                last_verified_event = ?
            WHERE request_id = ? AND owner_uid = ?
            """,
            (
                now,
                now,
                (
                    "Patient authorization revoked. Information already delivered to a "
                    "destination cannot be retrieved automatically."
                ),
                request_id,
                owner_uid,
            ),
        )
        _insert_event(
            connection,
            request_id,
            "patient",
            owner_uid,
            "consent_revoked",
            old_status,
            "revoked",
            now,
        )
        updated = connection.execute(
            "SELECT * FROM care_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return _row_to_request(connection, updated), True


def update_request_status(actor_uid, request_id, new_status, actor_type="workforce"):
    actor_uid = _clean_text(actor_uid, "Workforce user", 160, required=True)
    request_id = _clean_text(request_id, "Request identifier", 80, required=True)
    new_status = _clean_text(new_status, "Status", 40, required=True).lower()
    initialize_care_coordination_store()
    with _managed_connection() as connection:
        row = connection.execute(
            "SELECT * FROM care_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if row is None:
            raise CareCoordinationError("Care request not found.")
        old_status = row["status"]
        allowed = WORKFORCE_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            raise CareCoordinationError(
                f"Status cannot move from {old_status} to {new_status}."
            )

        now = _iso(_utc_now())
        externally_sent = new_status in EXTERNALLY_SENT_STATUSES
        external_delivery_status = (
            "verified_sent" if externally_sent else row["external_delivery_status"]
        )
        verified_event = {
            "ready_for_review": "Authorized request reviewed and ready for the next administrative step.",
            "needs_information": "Workforce review found that more information is needed.",
            "sent_to_destination": "Workforce user verified delivery to the selected destination.",
            "acknowledged": "Destination receipt was verified.",
            "accepted": "Destination acceptance was verified; appointment is not yet confirmed.",
            "scheduled": "Appointment date or authorization follow-up was verified.",
            "completed": "The administrative coordination request was marked complete.",
            "blocked": "The destination reported a barrier or declined the request.",
            "cancelled": "The coordination request was cancelled.",
        }[new_status]
        connection.execute(
            """
            UPDATE care_requests
            SET status = ?, updated_at = ?, external_delivery_status = ?,
                last_verified_event = ?
            WHERE request_id = ?
            """,
            (new_status, now, external_delivery_status, verified_event, request_id),
        )
        _insert_event(
            connection,
            request_id,
            actor_type,
            actor_uid,
            "status_updated",
            old_status,
            new_status,
            now,
        )
        updated = connection.execute(
            "SELECT * FROM care_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return _row_to_request(connection, updated)
