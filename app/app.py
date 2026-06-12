import sys
from pathlib import Path
import math
import json
import random
from difflib import get_close_matches

import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))

from flask import Flask, request, Response
from flask_cors import CORS
from models.recommendation_engine import recommend, encoders

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

    if isinstance(obj, list):
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


def confidence_score(providers, advocates, specialty):
    if specialty == "No exact AI specialty match":
        return 0

    score = 70

    if len(providers) > 0:
        score += 15

    if len(advocates) > 0:
        score += 10

    score += random.randint(0, 5)

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

        allowed_conditions = list(encoders["condition"].classes_)

        direct_matches = [
            condition for condition in allowed_conditions
            if typed_condition.lower() in str(condition).lower()
        ]

        close_matches = get_close_matches(
            typed_condition,
            allowed_conditions,
            n=5,
            cutoff=0.2
        )

        suggestions = []

        for item in direct_matches + close_matches:
            if item not in suggestions:
                suggestions.append(item)

        return json_response({
            "typed": typed_condition,
            "suggestions": suggestions[:5]
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
            "age": int(data.get("age", 0)),
            "gender": data.get("gender", ""),
            "city": data.get("city", ""),
            "insurance": data.get("insurance", ""),
            "condition": data.get("condition", ""),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude")
        }

        (
            specialty,
            providers,
            advocates,
            nearest_clinics,
            fallback_hospitals
        ) = recommend(patient)

        emergency = detect_emergency(patient["condition"])
        confidence = confidence_score(providers, advocates, specialty)

        ai_matched = specialty != "No exact AI specialty match"

        message = (
            "AI matched your condition to a specialty."
            if ai_matched
            else "This symptom was not found in the AI model, so nearby rural clinics and fallback care options are shown instead."
        )

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
            "ai_matched": ai_matched
        })

        return json_response({
            "success": True,
            "ai_matched": ai_matched,
            "message": message,
            "specialty": specialty,
            "confidence": confidence,
            "emergency": emergency,
            "providers": providers.head(5),
            "advocates": advocates.head(5),
            "nearest_clinics": nearest_clinics.head(3),
            "fallback_hospitals": fallback_hospitals.head(5)
        })

    except Exception as error:
        return json_response({
            "success": False,
            "ai_matched": False,
            "message": "CareConnect AI could not process this request, but the system is still running.",
            "error": str(error),
            "specialty": "No exact AI specialty match",
            "confidence": 0,
            "emergency": {
                "is_emergency": False,
                "message": "No emergency warning detected from the entered condition."
            },
            "providers": [],
            "advocates": [],
            "nearest_clinics": [],
            "fallback_hospitals": []
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
