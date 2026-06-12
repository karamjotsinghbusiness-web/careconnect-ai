import pandas as pd
import joblib
from pathlib import Path
from difflib import get_close_matches

from models.provider_matcher import (
    find_matching_providers,
    find_nearest_clinics,
    find_nearest_hospitals_or_clinics
)


BASE_DIR = Path(__file__).resolve().parent.parent
SAVE_DIR = BASE_DIR / "saved_models"
DATA_PATH = BASE_DIR / "data" / "missouri_healthcare_linked_dataset_with_rural_clinics.xlsx"


model = joblib.load(SAVE_DIR / "specialty_model.pkl")
encoders = joblib.load(SAVE_DIR / "encoders.pkl")
specialty_encoder = joblib.load(SAVE_DIR / "specialty_enocders.pkl")


def fix_value(column, value):
    allowed = list(encoders[column].classes_)

    if value in allowed:
        return value

    match = get_close_matches(
        str(value),
        allowed,
        n=1,
        cutoff=0.4
    )

    if match:
        print(f"Changed '{value}' to closest match: '{match[0]}'")
        return match[0]

    return None


def get_condition_suggestions(user_text, limit=5):
    allowed_conditions = list(encoders["condition"].classes_)

    user_text = str(user_text).lower().strip()

    direct_matches = [
        condition for condition in allowed_conditions
        if user_text in str(condition).lower()
    ]

    close_matches = get_close_matches(
        user_text,
        allowed_conditions,
        n=limit,
        cutoff=0.2
    )

    suggestions = []

    for item in direct_matches + close_matches:
        if item not in suggestions:
            suggestions.append(item)

    return suggestions[:limit]


def predict_specialty(patient):
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


def find_advocates(patient_city, top_n=5):
    advocates = load_advocates()

    patient_city = str(patient_city).lower().strip()

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
        top_n=5
    )

    nearest_clinics = find_nearest_clinics(
        patient_city=patient["city"],
        patient_latitude=patient_latitude,
        patient_longitude=patient_longitude,
        top_n=3
    )

    providers = pd.DataFrame()
    fallback_hospitals = pd.DataFrame()

    if predicted_specialty is not None:
        providers = find_matching_providers(
            predicted_specialty=predicted_specialty,
            patient_city=patient["city"],
            patient_latitude=patient_latitude,
            patient_longitude=patient_longitude,
            top_n=5
        )

    if predicted_specialty is None or providers.empty:
        fallback_hospitals = find_nearest_hospitals_or_clinics(
            patient_city=patient["city"],
            patient_latitude=patient_latitude,
            patient_longitude=patient_longitude,
            top_n=5
        )

    if predicted_specialty is None:
        predicted_specialty = "No exact AI specialty match"

    return (
        predicted_specialty,
        providers,
        advocates,
        nearest_clinics,
        fallback_hospitals
    )


if __name__ == "__main__":
    sample_patient = {
        "age": 55,
        "gender": "Male",
        "city": "Kansas City",
        "insurance": "Medicare",
        "condition": "Random Unknown Symptom",
        "latitude": None,
        "longitude": None
    }

    (
        specialty,
        providers,
        advocates,
        nearest_clinics,
        fallback_hospitals
    ) = recommend(sample_patient)

    print("\nRecommended Specialty:")
    print(specialty)

    print("\nMatching Providers:")
    print(providers)

    print("\nNearest Clinics:")
    print(nearest_clinics)

    print("\nFallback Hospitals / Clinics:")
    print(fallback_hospitals)

    print("\nMatching Advocates:")
    print(advocates)
