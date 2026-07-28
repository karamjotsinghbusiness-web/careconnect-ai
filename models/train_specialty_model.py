import pandas as pd
import joblib
from pathlib import Path

from sklearn.preprocessing import LabelEncoder
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "missouri_healthcare_linked_dataset_with_expanded_symptoms.xlsx"
SAVE_DIR = BASE_DIR / "saved_models"

SAVE_DIR.mkdir(exist_ok=True)

df = pd.read_excel(DATA_PATH, sheet_name="Patients")


features = ["age", "gender", "city", "insurance", "condition"]
target = "recommended_specialty"

df = df.dropna(subset=features + [target])

encoders = {}

for col in ["gender", "city", "insurance", "condition"]:
    encoder = LabelEncoder()
    df[col] = encoder.fit_transform(df[col].astype(str))
    encoders[col] = encoder

specialty_encoder = LabelEncoder()
df[target] = specialty_encoder.fit_transform(df[target].astype(str))

X = df[features]
y = df[target]

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

model = RandomForestClassifier(
    n_estimators=200,
    random_state=42
)

model.fit(X_train, y_train)

accuracy = model.score(X_test, y_test)

joblib.dump(model, SAVE_DIR / "specialty_model.pkl")
joblib.dump(encoders, SAVE_DIR / "encoders.pkl")

# keep your current misspelled file name because your app uses it
joblib.dump(specialty_encoder, SAVE_DIR / "specialty_enocders.pkl")

print("Training complete.")
print(f"Accuracy: {accuracy:.2%}")
print("Saved:")
print(SAVE_DIR / "specialty_model.pkl")
print(SAVE_DIR / "encoders.pkl")
print(SAVE_DIR / "specialty_enocders.pkl")