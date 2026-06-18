import sys
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR))
import pandas as pd
import joblib
from difflib import get_close_matches
from models.provider_matcher import (
    find_matching_providers,
    find_nearest_clinics,
    find_nearest_hospitals_or_clinics
)

from models.hospital_matcher import find_best_hospitals

from models.long_term import find_best_long_term_hospitals

BASE_DIR = Path(__file__).resolve().parent.parent
SAVE_DIR = BASE_DIR / "saved_models"
DATA_PATH = BASE_DIR / "data" / "missouri_healthcare_linked_dataset_with_expanded_symptoms.xlsx"


model = joblib.load(SAVE_DIR / "specialty_model.pkl")
encoders = joblib.load(SAVE_DIR / "encoders.pkl")
specialty_encoder = joblib.load(SAVE_DIR / "specialty_enocders.pkl")


NON_URGENT_CONDITION_TO_SPECIALTY = {
    "stomach pain": "Family Practice",
    "abdominal pain": "Family Practice",
    "belly pain": "Family Practice",
    "vomiting": "Family Practice",
    "throwing up": "Family Practice",
    "nausea": "Family Practice",
    "diarrhea": "Family Practice",
    "constipation": "Family Practice",
    "fever": "Family Practice",
    "cough": "Family Practice",
    "sore throat": "Family Practice",
    "runny nose": "Family Practice",
    "cold": "Family Practice",
    "flu": "Family Practice",
    "headache": "Family Practice",
    "dizziness": "Family Practice",
    "fatigue": "Family Practice",
    "ear pain": "Family Practice",
    "earache": "Family Practice",
    "sinus pain": "Family Practice",
    "allergies": "Family Practice",
    "rash": "Family Practice",
    "skin rash": "Family Practice",
    "itching": "Family Practice",
    "burning urination": "Family Practice",
    "uti": "Family Practice",
    "urinary pain": "Family Practice",

    "anxiety": "Mental Health Counselor",
    "stress": "Mental Health Counselor",
    "depression": "Mental Health Counselor",
    "sadness": "Mental Health Counselor",
    "panic attacks": "Mental Health Counselor",
    "panic attack": "Mental Health Counselor",
    "mental health": "Mental Health Counselor",

    "back pain": "Physical Therapist In Private Practice",
    "lower back pain": "Physical Therapist In Private Practice",
    "neck pain": "Physical Therapist In Private Practice",
    "shoulder pain": "Physical Therapist In Private Practice",
    "knee pain": "Physical Therapist In Private Practice",
    "ankle pain": "Physical Therapist In Private Practice",
    "wrist pain": "Physical Therapist In Private Practice",
    "muscle pain": "Physical Therapist In Private Practice",
    "joint pain": "Physical Therapist In Private Practice",

    "speech problem": "Qualified Speech Language Pathologist",
    "speech issues": "Qualified Speech Language Pathologist",
    "trouble speaking": "Qualified Speech Language Pathologist",

    "kidney pain": "Nephrology",
    "breathing problem": "Pulmonology",
    "shortness of breath": "Pulmonology",

    "chest pain": "Cardiovascular Disease (Cardiology)",
    "heart pain": "Cardiovascular Disease (Cardiology)",
    "heart problem": "Cardiovascular Disease (Cardiology)",
    "high blood pressure": "Cardiovascular Disease (Cardiology)"
}

SYMPTOM_KEYWORDS_TO_SPECIALTY = [
    (
        [
            "stomach",
            "abdominal",
            "abdomen",
            "belly",
            "tummy",
            "vomit",
            "nausea",
            "diarrhea",
            "constipation",
            "fever",
            "cough",
            "throat",
            "cold",
            "flu",
            "headache",
            "dizzy",
            "fatigue",
            "ear",
            "sinus",
            "allerg",
            "rash",
            "itch",
            "urination",
            "uti"
        ],
        "Family Practice"
    ),
    (
        [
            "anxiety",
            "stress",
            "depress",
            "sad",
            "panic",
            "mental"
        ],
        "Mental Health Counselor"
    ),
    (
        [
            "back",
            "neck",
            "shoulder",
            "knee",
            "ankle",
            "wrist",
            "muscle",
            "joint",
            "hip"
        ],
        "Physical Therapist In Private Practice"
    ),
    (
        [
            "speech",
            "speaking",
            "talking"
        ],
        "Qualified Speech Language Pathologist"
    ),
    (
        [
            "kidney"
        ],
        "Nephrology"
    ),
    (
        [
            "breath",
            "breathing",
            "shortness",
            "lung",
            "respiratory"
        ],
        "Pulmonology"
    ),
    (
        [
            "chest",
            "heart",
            "blood pressure",
            "cardiac"
        ],
        "Cardiovascular Disease (Cardiology)"
    )
]


def normalize_text(value):
    return str(value).lower().strip()


def manual_specialty_match(condition):
    condition = normalize_text(condition)

    if condition in NON_URGENT_CONDITION_TO_SPECIALTY:
        return NON_URGENT_CONDITION_TO_SPECIALTY[condition]

    keys = list(NON_URGENT_CONDITION_TO_SPECIALTY.keys())

    close = get_close_matches(
        condition,
        keys,
        n=1,
        cutoff=0.72
    )

    if close:
        return NON_URGENT_CONDITION_TO_SPECIALTY[close[0]]

    for key, specialty in NON_URGENT_CONDITION_TO_SPECIALTY.items():
        if key in condition or condition in key:
            return specialty

    for keywords, specialty in SYMPTOM_KEYWORDS_TO_SPECIALTY:
        if any(keyword in condition for keyword in keywords):
            return specialty

    return None


def fix_value(column, value):
    allowed = list(encoders[column].classes_)
    value_text = str(value).strip()

    for item in allowed:
        if str(item).lower().strip() == value_text.lower():
            return item

    match = get_close_matches(
        value_text,
        allowed,
        n=1,
        cutoff=0.35
    )

    if match:
        print(f"Changed '{value}' to closest match: '{match[0]}'")
        return match[0]

    return None


def get_condition_suggestions(user_text, limit=5):
    allowed_conditions = list(encoders["condition"].classes_)

    user_text_clean = normalize_text(user_text)

    manual_matches = [
        condition for condition in NON_URGENT_CONDITION_TO_SPECIALTY.keys()
        if user_text_clean in condition or condition in user_text_clean
    ]

    keyword_specialty = manual_specialty_match(user_text)

    if keyword_specialty is not None:
        manual_matches.append(keyword_specialty)

    direct_matches = [
        condition for condition in allowed_conditions
        if user_text_clean in str(condition).lower()
    ]

    close_manual = get_close_matches(
        user_text_clean,
        list(NON_URGENT_CONDITION_TO_SPECIALTY.keys()),
        n=limit,
        cutoff=0.25
    )

    close_model = get_close_matches(
        user_text_clean,
        allowed_conditions,
        n=limit,
        cutoff=0.2
    )

    suggestions = []

    for item in manual_matches + direct_matches + close_manual + close_model:
        if item not in suggestions:
            suggestions.append(item)

    return suggestions[:limit]


def predict_specialty(patient):
    manual_match = manual_specialty_match(patient.get("condition", ""))

    if manual_match is not None:
        print(
            f"Using manual symptom mapping: "
            f"{patient.get('condition')} -> {manual_match}"
        )
        return manual_match

    patient_copy = patient.copy()

    model_columns = [
        "age",
        "gender",
        "city",
        "insurance",
        "condition"
    ]

    patient_copy = {
        key: patient_copy[key]
        for key in model_columns
    }

    for col in ["gender", "city", "insurance", "condition"]:
        fixed_value = fix_value(col, patient_copy[col])

        if fixed_value is None:
            return None

        patient_copy[col] = encoders[col].transform([fixed_value])[0]

    input_data = pd.DataFrame([patient_copy])
    prediction = model.predict(input_data)

    specialty = specialty_encoder.inverse_transform(prediction)[0]

    return specialty


def load_advocates():
    advocates = pd.read_excel(
        DATA_PATH,
        sheet_name="Advocates"
    )

    advocates.columns = (
        advocates.columns
        .str.lower()
        .str.strip()
    )

    return advocates


def find_advocates(patient_city, condition=None, top_n=5):
    advocates = load_advocates()

    patient_city = normalize_text(patient_city)

    if "city" not in advocates.columns:
        return advocates.head(top_n)

    advocates["city_clean"] = (
        advocates["city"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    city_matches = advocates[
        advocates["city_clean"] == patient_city
    ].copy()

    if not city_matches.empty:
        return city_matches.head(top_n)

    return advocates.head(top_n)


def recommend(patient):
    predicted_specialty = predict_specialty(patient)

    patient_latitude = patient.get("latitude")
    patient_longitude = patient.get("longitude")

    advocates = find_advocates(
        patient_city=patient["city"],
        condition=patient.get("condition"),
        top_n=5
    )

    nearest_clinics = find_nearest_clinics(
        patient_city=patient["city"],
        patient_latitude=patient_latitude,
        patient_longitude=patient_longitude,
        top_n=5
    )

    providers = pd.DataFrame()

    if predicted_specialty is not None:
        providers = find_matching_providers(
            predicted_specialty=predicted_specialty,
            patient_city=patient["city"],
            patient_latitude=patient_latitude,
            patient_longitude=patient_longitude,
            top_n=10
        )

        if "distance_miles" in providers.columns:
            providers = providers[
                providers["distance_miles"].astype(str) != "Unknown"
            ].copy()

            providers = providers[
                providers["distance_miles"].astype(float) <= 30
            ].copy()

    fallback_hospitals = find_nearest_hospitals_or_clinics(
        patient_city=patient["city"],
        patient_latitude=patient_latitude,
        patient_longitude=patient_longitude,
        top_n=5
    )

    recommended_hospitals = find_best_hospitals(
        patient_city=patient["city"],
        condition=patient.get("condition", ""),
        top_n=5,
        radius_miles=60
    )
    recommended_long_term = find_best_long_term_hospitals(
        patient_city = patient["city"],
        condition = patient.get("condition", ""),
        top_n = 5,
        radius_miles= 80    
                    )

    if predicted_specialty is None:
        predicted_specialty = "No exact AI specialty match"

    return (
        predicted_specialty,
        providers.head(5),
        advocates.head(5),
        nearest_clinics.head(5),
        fallback_hospitals.head(5),
        recommended_hospitals.head(5)
    )


if __name__ == "__main__":
    sample_patient = {
        "age": 55,
        "gender": "Male",
        "city": "Springfield",
        "insurance": "Medicare",
        "condition": "Chest Pain",
        "latitude": None,
        "longitude": None
    }

    (
        specialty,
        providers,
        advocates,
        nearest_clinics,
        fallback_hospitals,
        recommended_long_term,
        recommended_hospitals
    ) = recommend(sample_patient)

    print("\nRecommended Specialty:")
    print(specialty)

    print("\nMatching Providers within 30 miles:")
    print(providers)

    print("\nNearest Rural Clinics:")
    print(nearest_clinics)

    print("\nNearest Fallback Hospitals / Clinics:")
    print(fallback_hospitals)

    print("\nRecommended Hospitals by Condition:")
    print(recommended_hospitals)

    print("\nMatching Advocates:")
    print(advocates)

    print("\nRecommended Long Term Hospitals by Conditions:")
    print(recommended_long_term)
