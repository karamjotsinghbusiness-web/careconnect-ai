import pandas as pd
import joblib
from pathlib import Path
from difflib import get_close_matches

BASE_DIR = Path(__file__).resolve().parent.parent
SAVE_DIR = BASE_DIR / "saved_models"

model = joblib.load(SAVE_DIR / "specialty_model.pkl")
encoders = joblib.load(SAVE_DIR / "encoders.pkl")
specialty_encoder = joblib.load(SAVE_DIR / "specialty_enocders.pkl")

patient = {
    "age": 55,
    "gender": "Male",
    "city": "Kansas City",
    "insurance": "Medicare",
    "condition": "Heart Disease"
}

def fix_value(column, value):
    allowed = list(encoders[column].classes_)

    if value in allowed:
        return value

    match = get_close_matches(value, allowed, n=1, cutoff=0.4)

    if match:
        print(f"Changed '{value}' to closest match: '{match[0]}'")
        return match[0]

    print(f"\nInvalid value for {column}: {value}")
    print("Allowed values:")
    print(allowed)
    raise ValueError(f"Could not match {value}")

for col in ["gender", "city", "insurance", "condition"]:
    fixed_value = fix_value(col, patient[col])
    patient[col] = encoders[col].transform([fixed_value])[0]

input_data = pd.DataFrame([patient])

prediction = model.predict(input_data)
recommended_specialty = specialty_encoder.inverse_transform(prediction)[0]

print("\nRecommended Specialty:")
print(recommended_specialty)