import sys
from pathlib import Path
import math
import json
import random

import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from flask import Flask, request, Response
from flask_cors import CORS

from models.recommendation_engine import recommend, get_condition_suggestions
from models.ai_explainer import explain_recommendation


app = Flask(__name__)
CORS(app)

search_history = []


def clean_data(obj):
    if obj is None:
        return None

    if isinstance(obj, float) and math.isnan(obj):
        return None

    if isinstance(obj, (np.integer,)):
        return int(obj)

    if isinstance(obj, (np.floating,)):
        if np.isnan(obj):
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

    return obj


def json_response(data, status=200):
    cleaned = clean_data(data)

    return Response(
        json.dumps(cleaned, allow_nan=False),
        status=status,
        mimetype="application/json"
    )


def as_dataframe(value):
    if isinstance(value, pd.DataFrame):
        return value

    if value is None:
        return pd.DataFrame()

    try:
        return pd.DataFrame(value)
    except Exception:
        return pd.DataFrame()


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


@app.route("/", methods=["GET"])
def home():
    return json_response({
        "message": "CareConnect AI backend is running"
    })


@app.route("/symptom_suggestions", methods=["POST", "OPTIONS"])
def symptom_suggestions():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})

    try:
        data = request.get_json() or {}
        typed_condition = str(data.get("condition", "")).strip()

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
        return json_response({
            "typed": "",
            "suggestions": []
        })


@app.route("/recommend", methods=["POST", "OPTIONS"])
def get_recommendation():
    if request.method == "OPTIONS":
        return json_response({"status": "ok"})

    try:
        data = request.get_json() or {}

        patient = {
            "age": int(data.get("age", 0) or 0),
            "gender": data.get("gender", ""),
            "city": data.get("city", ""),
            "insurance": data.get("insurance", ""),
            "condition": data.get("condition", ""),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude")
        }

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

        ai_matched = specialty != "No exact AI specialty match"

        if ai_matched:
            message = "AI matched your condition to a specialty."
        else:
            message = "This symptom was not found in the AI model, so nearby clinics, fallback care, hospital options, and long-term hospital options are shown instead."

        try:
            ai_explanation = explain_recommendation(
                patient=patient,
                specialty=specialty,
                providers=providers,
                advocates=advocates,
                hospitals=recommended_hospitals,
                hospices=None
            )
        except Exception:
            ai_explanation = "CareConnect AI explanation is currently unavailable, but the provider recommendations were still created."

        search_history.append({
            "city": patient["city"],
            "condition": patient["condition"],
            "specialty": specialty,
            "confidence": confidence,
            "latitude": patient["latitude"],
            "longitude": patient["longitude"],
            "provider_count": len(providers),
            "nearest_clinic_count": len(nearest_clinics),
            "fallback_hospital_count": len(fallback_hospitals),
            "recommended_hospital_count": len(recommended_hospitals),
            "recommended_long_term_count": len(recommended_long_term),
            "advocate_count": len(advocates),
            "ai_matched": ai_matched
        })

        return json_response({
            "success": True,
            "ai_matched": ai_matched,
            "message": message,
            "ai_explanation": ai_explanation,
            "specialty": specialty,
            "confidence": confidence,
            "emergency": emergency,
            "providers": providers.head(5),
            "advocates": advocates.head(5),
            "nearest_clinics": nearest_clinics.head(5),
            "fallback_hospitals": fallback_hospitals.head(5),
            "recommended_hospitals": recommended_hospitals.head(5),
            "recommended_long_term": recommended_long_term.head(5)
        })

    except Exception as error:
        return json_response({
            "success": False,
            "ai_matched": False,
            "message": "CareConnect AI could not process this request. Check app.py, recommendation_engine.py, provider_matcher.py, long_term.py, the Excel dataset, and Railway deployment.",
            "error": str(error),
            "ai_explanation": "AI explanation is unavailable because the recommendation request failed.",
            "specialty": "No exact AI specialty match",
            "confidence": 0,
            "emergency": {
                "is_emergency": False,
                "message": "No emergency warning detected from the entered condition."
            },
            "providers": [],
            "advocates": [],
            "nearest_clinics": [],
            "fallback_hospitals": [],
            "recommended_hospitals": [],
            "recommended_long_term": []
        }, status=200)


@app.route("/analytics", methods=["GET"])
def analytics():
    total_searches = len(search_history)

    specialty_counts = {}

    for item in search_history:
        specialty = item["specialty"]
        specialty_counts[specialty] = specialty_counts.get(specialty, 0) + 1

    return json_response({
        "total_searches": total_searches,
        "specialty_counts": specialty_counts,
        "recent_searches": search_history[-5:]
    })


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
