import pandas as pd
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "missouri_healthcare_linked_dataset_with_rural_clinics.xlsx"


def calculate_distance_miles(lat1, lon1, lat2, lon2):
    radius_miles = 3958.8

    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return round(radius_miles * c, 2)


def load_providers():
    providers = pd.read_excel(DATA_PATH, sheet_name="Providers")
    providers.columns = providers.columns.str.lower().str.strip()
    return providers


def add_distance(df, patient_latitude=None, patient_longitude=None):
    df = df.copy()

    if (
        patient_latitude is not None
        and patient_longitude is not None
        and "latitude" in df.columns
        and "longitude" in df.columns
    ):
        df = df.dropna(subset=["latitude", "longitude"]).copy()

        if df.empty:
            return df

        df["distance_miles"] = df.apply(
            lambda row: calculate_distance_miles(
                patient_latitude,
                patient_longitude,
                row["latitude"],
                row["longitude"]
            ),
            axis=1
        )

        df = df.sort_values("distance_miles")
    else:
        df["distance_miles"] = "Unknown"

    return df


def find_matching_providers(
    predicted_specialty,
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5
):
    providers = load_providers()

    predicted_specialty = str(predicted_specialty).lower().strip()
    patient_city = str(patient_city).lower().strip()

    providers["specialty_clean"] = providers["specialty"].astype(str).str.lower().str.strip()
    providers["city_clean"] = providers["city"].astype(str).str.lower().str.strip()

    matches = providers[
        providers["specialty_clean"].str.contains(
            predicted_specialty,
            na=False,
            regex=False
        )
    ].copy()

    if matches.empty:
        first_word = predicted_specialty.split()[0]

        matches = providers[
            providers["specialty_clean"].str.contains(
                first_word,
                na=False,
                regex=False
            )
        ].copy()

    if matches.empty:
        return pd.DataFrame()

    city_matches = matches[matches["city_clean"] == patient_city].copy()

    if not city_matches.empty:
        matches = city_matches

    matches = add_distance(matches, patient_latitude, patient_longitude)

    return matches.head(top_n)


def find_nearest_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=3
):
    providers = load_providers()

    providers["search_text"] = (
        providers.get("specialty", "").astype(str) + " " +
        providers.get("source", "").astype(str) + " " +
        providers.get("organization", "").astype(str) + " " +
        providers.get("provider_name", "").astype(str)
    ).str.lower()

    clinics = providers[
        providers["search_text"].str.contains(
            "rural health clinic|clinic|health center",
            na=False,
            regex=True
        )
    ].copy()

    if clinics.empty:
        return pd.DataFrame()

    clinics = add_distance(clinics, patient_latitude, patient_longitude)

    return clinics.head(top_n)


def find_nearest_hospitals_or_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5
):
    providers = load_providers()

    providers["search_text"] = (
        providers.get("specialty", "").astype(str) + " " +
        providers.get("source", "").astype(str) + " " +
        providers.get("organization", "").astype(str) + " " +
        providers.get("provider_name", "").astype(str)
    ).str.lower()

    hospitals = providers[
        providers["search_text"].str.contains(
            "hospital|medical center|health center|rural health clinic|clinic",
            na=False,
            regex=True
        )
    ].copy()

    if hospitals.empty:
        hospitals = providers.copy()

    hospitals = add_distance(hospitals, patient_latitude, patient_longitude)

    return hospitals.head(top_n)
