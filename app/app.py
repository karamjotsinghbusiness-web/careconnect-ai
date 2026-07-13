import os
import sys
import logging
from pathlib import Path
import math
import json
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from flask import Flask, request, Response
from flask_cors import CORS

from models.recommendation_engine import recommend, get_condition_suggestions, manual_specialty_match
from models.ai_explainer import explain_recommendation
from models.provider_discovery import discover_supplemental_resources, merge_supplemental
try:
    # Railway/Gunicorn loads this file as the app.app package module.
    from app.history_store import add_search, history_summary, initialize_history_store
except ImportError:
    # Keep direct local execution (`python app/app.py`) working too.
    from history_store import add_search, history_summary, initialize_history_store
try:
    from app.security import (
        initialize_firebase_admin,
        openai_phi_enabled,
        real_phi_enabled,
        require_admin,
        require_firebase_user,
    )
except ImportError:
    from security import (
        initialize_firebase_admin,
        openai_phi_enabled,
        real_phi_enabled,
        require_admin,
        require_firebase_user,
    )
try:
    from app.security_events import (
        initialize_security_events,
        recent_events,
        record_security_event,
        security_summary,
    )
except ImportError:
    from security_events import (
        initialize_security_events,
        recent_events,
        record_security_event,
        security_summary,
    )


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("careconnect")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
SUPPLEMENTAL_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="supplemental-search")

# These are the frontends allowed to call your Railway backend.
# Add more domains here if you later use a custom domain.
DEFAULT_ALLOWED_ORIGINS = [
    "https://careconnectai-19ace.firebaseapp.com",
    "https://careconnectai-19ace.web.app",
    "http://localhost:5500",
    "http://127.0.0.1:5500",
    "http://localhost:5000",
    "http://127.0.0.1:5000",
]

extra_origins = [
    origin.strip()
    for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]

allowed_origins = list(dict.fromkeys(DEFAULT_ALLOWED_ORIGINS + extra_origins))

CORS(
    app,
    resources={r"/*": {"origins": allowed_origins}},
    supports_credentials=False,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"]
)

MAX_SEARCH_HISTORY = 200
try:
    initialize_history_store()
except Exception as exc:
    logger.warning("Search history storage unavailable during startup: %s", type(exc).__name__)

try:
    initialize_security_events()
except Exception as exc:
    logger.warning("Security event storage unavailable during startup: %s", type(exc).__name__)
initialize_firebase_admin()

if os.environ.get("ALLOW_REAL_PHI", "false").lower() == "true" and not real_phi_enabled():
    record_security_event(
        "unsafe_phi_configuration", "critical", "startup",
        details={"configuration": "ALLOW_REAL_PHI requested without all required BAA confirmations"},
    )


def clean_data(obj):
    if obj is None:
        return None

    if isinstance(obj, (str, bool, int)):
        return obj

    if isinstance(obj, (date, datetime, pd.Timestamp)):
        if pd.isna(obj):
            return None
        return obj.isoformat()

    if isinstance(obj, Path):
        return str(obj)

    if isinstance(obj, (np.bool_,)):
        return bool(obj)

    if isinstance(obj, (np.integer,)):
        return int(obj)

    if isinstance(obj, (float, np.floating)):
        if not math.isfinite(float(obj)):
            return None
        return float(obj)

    if isinstance(obj, pd.DataFrame):
        return clean_data(obj.to_dict(orient="records"))

    if isinstance(obj, pd.Series):
        return clean_data(obj.to_dict())

    if isinstance(obj, list):
        return [clean_data(item) for item in obj]

    if isinstance(obj, tuple):
        return [clean_data(item) for item in obj]

    if isinstance(obj, dict):
        return {key: clean_data(value) for key, value in obj.items()}

    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass

    return obj


def json_response(data, status=200):
    cleaned = clean_data(data)

    return Response(
        json.dumps(cleaned, allow_nan=False),
        status=status,
        mimetype="application/json"
    )


@app.after_request
def add_security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def as_dataframe(value):
    if isinstance(value, pd.DataFrame):
        return value

    if value is None:
        return pd.DataFrame()

    try:
        return pd.DataFrame(value)
    except Exception:
        return pd.DataFrame()


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def detect_emergency(condition):
    condition = str(condition).lower()

    emergency_words = [
        "chest pain",
        "can't breathe",
        "cant breathe",
        "difficulty breathing",
        "stroke",
        "heart attack",
        "severe bleeding",
        "unconscious",
        "seizure"
    ]

    for word in emergency_words:
        if word in condition:
            return {
                "is_emergency": True,
                "message": "Possible urgent symptoms detected. Seek immediate medical help or call emergency services if this is serious."
            }

    return {
        "is_emergency": False,
        "message": "No emergency warning detected from the entered condition."
    }


def confidence_score(
    providers,
    advocates,
    nearest_clinics,
    fallback_hospitals,
    recommended_hospitals,
    recommended_long_term,
    specialty
):
    if specialty == "No exact AI specialty match":
        score = 35
    else:
        score = 70

    if len(providers) > 0:
        score += 15

    if len(advocates) > 0:
        score += 8

    if len(nearest_clinics) > 0:
        score += 8

    if len(fallback_hospitals) > 0:
        score += 5

    if len(recommended_hospitals) > 0:
        score += 7

    if len(recommended_long_term) > 0:
        score += 7

    score += random.randint(0, 3)

    return min(score, 98)


def _count_rows(value):
    try:
        return len(value)
    except Exception:
        return 0


def _min_distance_from_frames(*frames):
    distances = []

    for frame in frames:
        if frame is None or getattr(frame, "empty", True):
            continue

        if "distance_miles" not in frame.columns:
            continue

        values = pd.to_numeric(frame["distance_miles"], errors="coerce").dropna()

        if len(values) > 0:
            distances.append(float(values.min()))

    if not distances:
        return None

    return min(distances)


def build_access_score(
    providers,
    advocates,
    nearest_clinics,
    fallback_hospitals,
    recommended_hospitals,
    recommended_long_term,
    specialty,
    patient
):
    """
    Business feature:
    Creates a navigation/access score. This is not a medical score.
    It only measures how many useful care-navigation options were found.
    """

    provider_count = _count_rows(providers)
    advocate_count = _count_rows(advocates)
    clinic_count = _count_rows(nearest_clinics)
    fallback_count = _count_rows(fallback_hospitals)
    hospital_count = _count_rows(recommended_hospitals)
    long_term_count = _count_rows(recommended_long_term)

    specialty_points = 0 if specialty == "No exact AI specialty match" else 25
    provider_points = min(provider_count * 6, 20)
    advocate_points = min(advocate_count * 5, 10)
    clinic_points = min(clinic_count * 4, 12)
    hospital_points = min(hospital_count * 4, 12)
    fallback_points = min(fallback_count * 3, 8)
    long_term_points = min(long_term_count * 3, 6)

    nearest_distance = _min_distance_from_frames(
        providers,
        nearest_clinics,
        fallback_hospitals,
        recommended_hospitals,
        recommended_long_term
    )

    if nearest_distance is None:
        distance_points = 0
        distance_label = "No distance data found"
    elif nearest_distance <= 10:
        distance_points = 15
        distance_label = "Strong nearby access"
    elif nearest_distance <= 25:
        distance_points = 12
        distance_label = "Good nearby access"
    elif nearest_distance <= 50:
        distance_points = 8
        distance_label = "Moderate travel needed"
    else:
        distance_points = 4
        distance_label = "Long travel may be needed"

    total = (
        specialty_points
        + provider_points
        + advocate_points
        + clinic_points
        + hospital_points
        + fallback_points
        + long_term_points
        + distance_points
    )

    total = min(int(total), 100)

    if total >= 80:
        level = "Strong Access"
    elif total >= 60:
        level = "Moderate Access"
    elif total >= 40:
        level = "Limited Access"
    else:
        level = "Care Gap Risk"

    return {
        "overall": total,
        "level": level,
        "nearest_distance_miles": nearest_distance,
        "distance_label": distance_label,
        "breakdown": {
            "specialty_match": specialty_points,
            "matching_providers": provider_points,
            "advocate_support": advocate_points,
            "nearby_clinics": clinic_points,
            "condition_based_hospitals": hospital_points,
            "fallback_options": fallback_points,
            "long_term_options": long_term_points,
            "distance_access": distance_points
        },
        "summary": (
            f"{level}: CareConnect found {provider_count} matching providers, "
            f"{clinic_count} nearby clinic options, {hospital_count} condition-based hospital options, "
            f"and {advocate_count} support resources."
        )
    }


def build_care_gap(
    providers,
    advocates,
    nearest_clinics,
    fallback_hospitals,
    recommended_hospitals,
    recommended_long_term,
    specialty
):
    """
    Business feature:
    Detects possible navigation/access gaps from the available public data.
    This is not a diagnosis or medical shortage declaration.
    """

    reasons = []

    if specialty == "No exact AI specialty match":
        reasons.append("No exact specialty match was found from the AI model.")

    if _count_rows(providers) == 0:
        reasons.append("No matching specialty providers were found in the returned results.")

    if _count_rows(nearest_clinics) == 0:
        reasons.append("No nearby rural clinic results were found.")

    if _count_rows(advocates) == 0:
        reasons.append("No care support specialists or advocates were found.")

    if _count_rows(recommended_hospitals) == 0 and _count_rows(fallback_hospitals) == 0:
        reasons.append("No hospital or fallback care options were found in the returned results.")

    nearest_distance = _min_distance_from_frames(
        providers,
        nearest_clinics,
        fallback_hospitals,
        recommended_hospitals,
        recommended_long_term
    )

    if nearest_distance is not None and nearest_distance > 50:
        reasons.append(f"The closest returned care option appears to be about {round(nearest_distance, 1)} miles away.")

    return {
        "detected": len(reasons) > 0,
        "risk_level": "High" if len(reasons) >= 3 else "Medium" if len(reasons) >= 1 else "Low",
        "reasons": reasons
    }


def build_care_route(patient, specialty, emergency, access_score, care_gap):
    """
    Business feature:
    Routes the user to a safe navigation path.
    This does not diagnose. It only organizes care options.
    """

    condition = str(patient.get("condition", "")).lower()

    if emergency.get("is_emergency"):
        route_type = "Emergency Warning Route"
        urgency_level = "Emergency Warning"
        steps = [
            "Seek immediate medical help or call emergency services if symptoms are serious.",
            "Use nearby hospitals or emergency care options first.",
            "After urgent needs are addressed, review follow-up providers and support resources."
        ]

    elif any(word in condition for word in ["hospice", "terminal", "end-stage", "end stage", "comfort care"]):
        route_type = "Serious Illness Support Route"
        urgency_level = "Supportive Care"
        steps = [
            "Review hospice or serious illness support options.",
            "Contact a licensed healthcare professional to discuss whether this type of support is appropriate.",
            "Use advocate support if the family needs help understanding insurance, care planning, or next steps."
        ]

    elif any(word in condition for word in ["rehab", "long term", "long-term", "ventilator", "mobility", "wound"]):
        route_type = "Long-Term Care Navigation Route"
        urgency_level = "Planned / Ongoing Care"
        steps = [
            "Review long-term care hospital or rehabilitation-related options.",
            "Compare distance and quality-related public measures.",
            "Ask the facility about referral requirements, insurance, and availability."
        ]

    elif specialty != "No exact AI specialty match":
        route_type = "Specialty Care Route"
        urgency_level = "Routine or Soon Care"
        steps = [
            f"Start with the recommended specialty: {specialty}.",
            "Check matching providers by distance and accepting-new-patient status.",
            "Use hospital or clinic fallback options if specialty access is limited.",
            "Use an advocate if scheduling, insurance, transportation, or coordination is difficult."
        ]

    else:
        route_type = "Fallback Care Route"
        urgency_level = "Navigation Needed"
        steps = [
            "No exact specialty match was found, so start with nearby clinics or primary care options.",
            "Use fallback hospitals or clinics if there are limited provider matches.",
            "Use support specialists or advocates to help decide where to call first."
        ]

    if care_gap.get("detected"):
        steps.append("Care gap signals were detected, so consider contacting more than one option and asking about availability.")

    return {
        "route_type": route_type,
        "urgency_level": urgency_level,
        "steps": steps,
        "access_level": access_score.get("level", "Unknown")
    }


def build_next_best_actions(patient, specialty, providers, advocates, nearest_clinics, recommended_hospitals, care_gap):
    """
    Business feature:
    Gives practical next steps instead of only listing providers.
    """

    actions = []

    if _count_rows(providers) > 0:
        actions.append("Call the top matching provider and ask if they are accepting new patients.")

    if _count_rows(nearest_clinics) > 0:
        actions.append("If the provider wait time is too long, call the nearest clinic or health center as a backup option.")

    if _count_rows(recommended_hospitals) > 0:
        actions.append("Review the recommended hospital options if symptoms may need hospital-level care or testing.")

    actions.append("Ask if your insurance is accepted before scheduling.")
    actions.append("Ask whether a referral is required.")
    actions.append("Write down your symptoms, medications, and questions before calling.")

    if _count_rows(advocates) > 0:
        actions.append("Contact a care support specialist if you need help with scheduling, insurance, transportation, or understanding options.")

    if care_gap.get("detected"):
        actions.append("Because access may be limited, contact at least two care options instead of waiting on only one.")

    return actions[:7]


def build_navigation_questions(patient):
    """
    Business feature:
    Intake-style questions that make the app feel like a healthcare navigation system.
    """

    return [
        "Are you trying to find a doctor, hospital, clinic, long-term care option, or support service?",
        "Do you need help with insurance, transportation, scheduling, or understanding where to go?",
        "Are you looking for the closest option, the highest-quality option, or the fastest available option?",
        "Do you need telehealth, language support, or low-cost care?",
        "Do you already have a referral, or do you need a provider that does not require one?"
    ]


def build_business_intelligence(
    patient,
    access_score,
    care_gap,
    providers,
    advocates,
    nearest_clinics,
    recommended_hospitals
):
    """
    Business feature:
    A small patient-facing intelligence summary. Later this can power an admin/pilot dashboard.
    """

    signals = []

    if access_score.get("overall", 0) < 60:
        signals.append("This search may represent a limited-access navigation case.")

    if _count_rows(providers) == 0:
        signals.append("Specialty provider availability appears limited in the returned results.")

    if _count_rows(advocates) == 0:
        signals.append("Care coordination support may be limited for this search.")

    if _count_rows(nearest_clinics) > 0:
        signals.append("Nearby clinic fallback options were found.")

    if _count_rows(recommended_hospitals) > 0:
        signals.append("Condition-based hospital quality options were found.")

    if not signals:
        signals.append("CareConnect found multiple navigation options for this search.")

    return {
        "city": patient.get("city", ""),
        "condition": patient.get("condition", ""),
        "signals": signals,
        "care_gap_detected": care_gap.get("detected", False),
        "access_level": access_score.get("level", "Unknown")
    }



@app.route("/", methods=["GET"])
def home():
    return json_response({
        "message": "CareConnect AI backend is running",
        "real_phi_enabled": real_phi_enabled(),
        "openai_phi_enabled": openai_phi_enabled()
    })


@app.route("/security/status", methods=["GET", "OPTIONS"])
@require_admin(json_response)
def security_status():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})
    return json_response({
        "success": True,
        "deployment_version": os.environ.get("DEPLOYMENT_VERSION", "unknown"),
        "real_phi_enabled": real_phi_enabled(),
        "openai_phi_enabled": openai_phi_enabled(),
        "alerts_configured": bool(
            os.environ.get("SECURITY_ALERT_WEBHOOK_URL")
            or os.environ.get("SECURITY_ALERT_EMAILS")
        ),
        "events": security_summary(),
    })


@app.route("/security/events", methods=["GET", "OPTIONS"])
@require_admin(json_response)
def security_event_list():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})
    return json_response({"success": True, "events": recent_events(request.args.get("limit", 50))})


@app.route("/symptom_suggestions", methods=["POST", "OPTIONS"])
@require_firebase_user(json_response)
def symptom_suggestions():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})

    try:
        data = request.get_json(silent=True) or {}
        typed_condition = str(data.get("condition", "")).strip()[:200]

        if typed_condition == "":
            return json_response({
                "typed": "",
                "suggestions": []
            })

        return json_response({
            "typed": typed_condition,
            "suggestions": get_condition_suggestions(typed_condition, limit=5)
        })

    except Exception:
        logger.exception("symptom_suggestions failed")
        return json_response({
            "typed": "",
            "suggestions": []
        })


@app.route("/recommend", methods=["POST", "OPTIONS"])
@require_firebase_user(json_response)
def get_recommendation():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})

    try:
        data = request.get_json(silent=True) or {}

        demo_only = data.get("demo_only_confirmed") is True
        if not real_phi_enabled() and not demo_only:
            return json_response({
                "success": False,
                "message": (
                    "Real patient information is disabled until hosting and Google BAAs "
                    "are confirmed. Use fictional demonstration data only."
                )
            }, status=403)

        patient = {
            "age": safe_int(data.get("age", 0), default=0),
            "gender": str(data.get("gender", ""))[:50],
            "city": str(data.get("city", ""))[:100],
            "insurance": str(data.get("insurance", ""))[:100],
            "condition": str(data.get("condition", ""))[:300],
            "latitude": data.get("latitude") if real_phi_enabled() else None,
            "longitude": data.get("longitude") if real_phi_enabled() else None
        }

        supplemental_limit = max(
            0, min(safe_int(os.environ.get("OPENAI_SEARCH_RESULT_LIMIT", 3), default=3), 5)
        )
        supplemental_future = None
        if supplemental_limit and (demo_only or openai_phi_enabled()):
            specialty_hint = manual_specialty_match(patient["condition"]) or "Primary Care"
            supplemental_future = SUPPLEMENTAL_EXECUTOR.submit(
                discover_supplemental_resources,
                city=patient["city"],
                specialty=specialty_hint,
                condition=patient["condition"],
                limit=supplemental_limit,
            )

        result = recommend(patient)

        specialty = "No exact AI specialty match"
        providers = pd.DataFrame()
        advocates = pd.DataFrame()
        nearest_clinics = pd.DataFrame()
        fallback_hospitals = pd.DataFrame()
        recommended_hospitals = pd.DataFrame()
        recommended_long_term = pd.DataFrame()

        if len(result) == 7:
            (
                specialty,
                providers,
                advocates,
                nearest_clinics,
                fallback_hospitals,
                recommended_hospitals,
                recommended_long_term
            ) = result

        elif len(result) == 6:
            (
                specialty,
                providers,
                advocates,
                nearest_clinics,
                fallback_hospitals,
                recommended_hospitals
            ) = result

        elif len(result) == 5:
            (
                specialty,
                providers,
                advocates,
                nearest_clinics,
                fallback_hospitals
            ) = result

        else:
            raise ValueError(
                f"recommend() returned {len(result)} values, but app.py expected 5, 6, or 7."
            )

        providers = as_dataframe(providers)
        advocates = as_dataframe(advocates)
        nearest_clinics = as_dataframe(nearest_clinics)
        fallback_hospitals = as_dataframe(fallback_hospitals)
        recommended_hospitals = as_dataframe(recommended_hospitals)
        recommended_long_term = as_dataframe(recommended_long_term)

        try:
            (
                supplemental_providers,
                supplemental_clinics,
                supplemental_advocates,
            ) = (
                supplemental_future.result(timeout=19)
                if supplemental_future is not None
                else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
            )
        except Exception as exc:
            logger.warning("Supplemental search unavailable: %s", type(exc).__name__)
            supplemental_providers = pd.DataFrame()
            supplemental_clinics = pd.DataFrame()
            supplemental_advocates = pd.DataFrame()
        providers = merge_supplemental(
            providers,
            supplemental_providers,
            ("provider_name", "facility_name"),
            dataset_limit=5,
            supplemental_limit=supplemental_limit,
        )
        nearest_clinics = merge_supplemental(
            nearest_clinics,
            supplemental_clinics,
            ("clinic_name", "provider_name", "facility_name"),
            dataset_limit=5,
            supplemental_limit=supplemental_limit,
        )
        advocates = merge_supplemental(
            advocates,
            supplemental_advocates,
            ("advocate_name", "provider_name"),
            dataset_limit=5,
            supplemental_limit=supplemental_limit,
        )

        emergency = detect_emergency(patient["condition"])

        confidence = confidence_score(
            providers,
            advocates,
            nearest_clinics,
            fallback_hospitals,
            recommended_hospitals,
            recommended_long_term,
            specialty
        )

        access_score = build_access_score(
            providers,
            advocates,
            nearest_clinics,
            fallback_hospitals,
            recommended_hospitals,
            recommended_long_term,
            specialty,
            patient
        )

        care_gap = build_care_gap(
            providers,
            advocates,
            nearest_clinics,
            fallback_hospitals,
            recommended_hospitals,
            recommended_long_term,
            specialty
        )

        care_route = build_care_route(
            patient,
            specialty,
            emergency,
            access_score,
            care_gap
        )

        next_best_actions = build_next_best_actions(
            patient,
            specialty,
            providers,
            advocates,
            nearest_clinics,
            recommended_hospitals,
            care_gap
        )

        navigation_questions = build_navigation_questions(patient)

        business_intelligence = build_business_intelligence(
            patient,
            access_score,
            care_gap,
            providers,
            advocates,
            nearest_clinics,
            recommended_hospitals
        )

        ai_matched = specialty != "No exact AI specialty match"

        if ai_matched:
            message = "AI matched your condition to a specialty."
        else:
            message = "This symptom was not found in the AI model, so nearby clinics, fallback care, hospital options, and long-term hospital options are shown instead."

        if os.environ.get("OPENAI_API_KEY") and os.environ.get(
            "ENABLE_OPENAI_PROVIDER_SEARCH", "true"
        ).lower() == "true":
            # Supplemental web search already used the request's OpenAI time
            # budget. Avoid a second sequential API call that can push Railway
            # beyond its 30-second edge limit.
            ai_explanation = (
                f"CareConnect matched the fictional concern to {specialty} and organized nearby "
                "care options from the dataset and available public listings. Confirm supplemental "
                "web results directly before use and contact a licensed healthcare professional for "
                "medical decisions."
            )
        else:
            try:
                if not (demo_only or openai_phi_enabled()):
                    raise RuntimeError("OpenAI PHI processing is disabled until BAA controls are confirmed")
                ai_explanation = explain_recommendation(
                    patient=patient,
                    specialty=specialty,
                    providers=providers,
                    advocates=advocates,
                    hospitals=recommended_hospitals,
                    hospices=None
                )
            except Exception:
                logger.exception("explain_recommendation failed")
                ai_explanation = "CareConnect AI explanation is currently unavailable, but the provider recommendations were still created."

        # Analytics storage must never erase a valid recommendation. Railway
        # volumes can be temporarily unavailable or SQLite can be locked.
        try:
            add_search({
                "specialty": specialty,
                "provider_count": len(providers),
                "nearest_clinic_count": len(nearest_clinics),
                "fallback_hospital_count": len(fallback_hospitals),
                "recommended_hospital_count": len(recommended_hospitals),
                "recommended_long_term_count": len(recommended_long_term),
                "advocate_count": len(advocates),
                "ai_matched": ai_matched,
                "access_score": access_score.get("overall"),
                "access_level": access_score.get("level"),
                "care_gap_detected": care_gap.get("detected")
            }, max_records=MAX_SEARCH_HISTORY)
        except Exception as exc:
            logger.warning(
                "Search history write failed; returning recommendation without history: %s",
                type(exc).__name__,
            )
            record_security_event(
                "search_history_write_failed",
                "medium",
                request.path,
                details={"reason": "storage_unavailable"},
            )

        return json_response({
            "success": True,
            "ai_matched": ai_matched,
            "message": message,
            "ai_explanation": ai_explanation,
            "specialty": specialty,
            "confidence": confidence,
            "emergency": emergency,
            "access_score": access_score,
            "care_gap": care_gap,
            "care_route": care_route,
            "next_best_actions": next_best_actions,
            "navigation_questions": navigation_questions,
            "business_intelligence": business_intelligence,
            "providers": providers,
            "advocates": advocates,
            "nearest_clinics": nearest_clinics,
            "fallback_hospitals": fallback_hospitals.head(5),
            "recommended_hospitals": recommended_hospitals.head(5),
            "recommended_long_term": recommended_long_term.head(5)
        })

    except Exception:
        logger.exception("get_recommendation failed")
        return json_response({
            "success": False,
            "error_code": "recommendation_failed",
            "ai_matched": False,
            "message": "CareConnect AI could not process this request. Please try again shortly.",
            "ai_explanation": "AI explanation is unavailable because the recommendation request failed.",
            "specialty": "No exact AI specialty match",
            "confidence": 0,
            "emergency": {
                "is_emergency": False,
                "message": "No emergency warning detected from the entered condition."
            },
            "access_score": {
                "overall": 0,
                "level": "Unavailable",
                "breakdown": {},
                "summary": "Access score unavailable because the recommendation request failed."
            },
            "care_gap": {
                "detected": False,
                "risk_level": "Unavailable",
                "reasons": []
            },
            "care_route": {
                "route_type": "Unavailable",
                "urgency_level": "Unavailable",
                "steps": []
            },
            "next_best_actions": [],
            "navigation_questions": [],
            "business_intelligence": {
                "signals": []
            },
            "providers": [],
            "advocates": [],
            "nearest_clinics": [],
            "fallback_hospitals": [],
            "recommended_hospitals": [],
            "recommended_long_term": []
        }, status=200)


@app.route("/analytics", methods=["GET", "OPTIONS"])
@require_admin(json_response)
def analytics():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})

    try:
        return json_response(history_summary(recent_limit=5))
    except Exception:
        logger.exception("Analytics storage unavailable")
        return json_response({
            "success": False,
            "message": "Analytics storage is temporarily unavailable."
        }, status=503)


if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))

    app.run(
        host=host,
        port=port,
        debug=debug_mode
    )
