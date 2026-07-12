import functools
import json
import os

import firebase_admin
from firebase_admin import auth, credentials
from flask import g, request

try:
    from app.security_events import count_recent, record_security_event
except ImportError:
    from security_events import count_recent, record_security_event


def env_true(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def initialize_firebase_admin():
    if not firebase_admin._apps:
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "careconnectai-19ace")
        options = {"projectId": project_id}
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()

        if service_account_json:
            service_account = json.loads(service_account_json)
            credential_project = service_account.get("project_id")
            if credential_project != project_id:
                raise ValueError(
                    "FIREBASE_SERVICE_ACCOUNT_JSON project_id does not match FIREBASE_PROJECT_ID"
                )
            firebase_admin.initialize_app(credentials.Certificate(service_account), options=options)
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
                g.firebase_user = auth.verify_id_token(header[7:], check_revoked=True)
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
                g.firebase_user = auth.verify_id_token(header[7:], check_revoked=True)
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
