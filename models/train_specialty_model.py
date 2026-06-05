import pandas as pd
import joblib
from pathlib import Path

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "/Users/karamjotsingh/Desktop/Joshuas_system/healthcare-ai/data/missouri_healthcare_linked_dataset.xlsx"
SAVE_DIR = BASE_DIR / "saved_models"

SAVE_DIR.mkdir(exist_ok=True)

df = pd.read_excel(
    DATA_PATH,
    sheet_name="Patients"
)

print("Loaded Data:")
print(df.head())
print("\n Columns:")
print(df.columns.tolist())

needed_columns = ["age", "gender", "city", "insurance", "condition", "recommended_specialty"]

for col in needed_columns:
    if col not in df.columns:
        raise ValueError(f"Missing Column: {col}")
df = df.dropna(subset=needed_columns)

encoders = {}

for col in ["gender", "city", "insurance", "condition"]:
    encoder = LabelEncoder()
    df[col] = encoder.fit_transform(df[col].astype(str))
    encoders[col] = encoder
target_encoder = LabelEncoder()
df["recommended_specialty"] = target_encoder.fit_transform(
    df["recommended_specialty"].astype(str)
)

X = df[["age", "gender", "city", "insurance", "condition"]]
y = df["recommended_specialty"]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)
model = RandomForestClassifier(
    n_estimators=200,
    random_state= 42
)

model.fit(X_train, y_train)

predictions = model.predict(X_test)

accuracy = accuracy_score(y_test, predictions)

print("\nModel Accuracy")
print(round(accuracy * 100, 2), "%")

print("\nClassifaction report:")
print(classification_report(y_test, predictions))

joblib.dump(model, SAVE_DIR / "specialty_model.pkl")
joblib.dump(encoders, SAVE_DIR / "encoders.pkl")
joblib.dump(target_encoder, SAVE_DIR / "specialty_enocders.pkl")

print("\nSaved Model")

