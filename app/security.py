import base64
import functools
import json
import os

import firebase_admin
from google.auth import exceptions as google_auth_exceptions
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token as google_id_token
from firebase_admin import auth, credentials
from flask import g, request

try:
    from app.security_events import count_recent, record_security_event
except ImportError:
    from security_events import count_recent, record_security_event


_admin_credential_unavailable = False


def env_true(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def initialize_firebase_admin():
    if not firebase_admin._apps:
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "careconnectai-19ace")
        options = {"projectId": project_id}
        service_account_base64 = os.environ.get("FIREBASE_SERVICE_ACCOUNT_BASE64", "").strip()
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

        if service_account_base64 or service_account_json:
            try:
                if service_account_base64:
                    decoded = base64.b64decode(service_account_base64, validate=True).decode("utf-8")
                    service_account = json.loads(decoded)
                else:
                    service_account = json.loads(service_account_json)
                if service_account.get("project_id") != project_id:
                    raise ValueError("service account project does not match configured project")
                credential = credentials.Certificate(service_account)
            except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError):
                # Keep the service available but authentication closed. Token
                # verification will fail without valid server credentials.
                print("Firebase Admin credential is malformed; authentication remains unavailable.", flush=True)
                firebase_admin.initialize_app(options=options)
                return

            firebase_admin.initialize_app(credential, options=options)
            return

        # Local development and secret-file deployments may use Application
        # Default Credentials through GOOGLE_APPLICATION_CREDENTIALS.
        firebase_admin.initialize_app(options=options)


def real_phi_enabled():
    return all([
        env_true("ALLOW_REAL_PHI"),
        env_true("HOSTING_BAA_CONFIRMED"),
        env_true("GOOGLE_BAA_CONFIRMED"),
    ])


def openai_phi_enabled():
    return all([
        real_phi_enabled(),
        env_true("OPENAI_BAA_CONFIRMED"),
        env_true("OPENAI_MODIFIED_RETENTION_CONFIRMED"),
    ])


def verify_firebase_user_token(token, allow_public_fallback=True):
    """Verify ordinary users even when Railway's Admin credential is unavailable.

    The fallback still verifies signature, issuer, audience, and expiry against
    Google's Firebase public certificates. Revocation checks need Admin
    credentials, so privileged endpoints never use this fallback.
    """
    global _admin_credential_unavailable

    if not (allow_public_fallback and _admin_credential_unavailable):
        try:
            return auth.verify_id_token(token, check_revoked=True)
        except (google_auth_exceptions.DefaultCredentialsError, google_auth_exceptions.RefreshError):
            if not allow_public_fallback:
                raise
            _admin_credential_unavailable = True

    if allow_public_fallback:
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "careconnectai-19ace")
        decoded = google_id_token.verify_firebase_token(
            token,
            google_auth_requests.Request(),
            audience=project_id,
        )
        decoded["uid"] = decoded.get("uid") or decoded.get("user_id") or decoded.get("sub")
        return decoded
    raise google_auth_exceptions.DefaultCredentialsError(
        "Firebase Admin credentials are unavailable"
    )


def require_firebase_user(json_response):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if request.method == "OPTIONS":
                return view(*args, **kwargs)

            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                record_security_event(
                    "authentication_missing", "medium", request.path,
                    source=request.remote_addr, details={"reason": "missing_bearer_token"}
                )
                return json_response({"success": False, "message": "Authentication required."}, 401)

            try:
                g.firebase_user = verify_firebase_user_token(header[7:])
            except Exception:
                record_security_event(
                    "authentication_invalid", "medium", request.path,
                    source=request.remote_addr, details={"reason": "invalid_or_revoked_token"}
                )
                failures = count_recent("authentication_invalid", source=request.remote_addr, minutes=5)
                if failures in {5, 20}:
                    record_security_event(
                        "authentication_failure_threshold",
                        "high" if failures == 5 else "critical",
                        request.path,
                        source=request.remote_addr,
                        details={"count": failures, "window_minutes": 5},
                    )
                return json_response({"success": False, "message": "Invalid or expired session."}, 401)

            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_admin(json_response):
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if request.method == "OPTIONS":
                return view(*args, **kwargs)

            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                record_security_event(
                    "admin_authentication_missing", "high", request.path,
                    source=request.remote_addr, details={"reason": "missing_bearer_token"}
                )
                return json_response({"success": False, "message": "Authentication required."}, 401)
            try:
                g.firebase_user = verify_firebase_user_token(
                    header[7:], allow_public_fallback=False
                )
            except Exception:
                record_security_event(
                    "admin_authentication_invalid", "high", request.path,
                    source=request.remote_addr, details={"reason": "invalid_or_revoked_token"}
                )
                return json_response({"success": False, "message": "Invalid or expired session."}, 401)
            if g.firebase_user.get("admin") is not True:
                record_security_event(
                    "admin_access_denied", "high", request.path,
                    actor=g.firebase_user.get("uid"), source=request.remote_addr,
                    details={"reason": "admin_claim_required"},
                )
                return json_response({"success": False, "message": "Administrator access required."}, 403)
            return view(*args, **kwargs)
        return wrapped
    return decorator
