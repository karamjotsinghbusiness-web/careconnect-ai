import pandas as pd
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2


BASE_DIR = Path(__file__).resolve().parent.parent

DATA_PATH = (
    BASE_DIR
    / "data"
    / "missouri_healthcare_linked_dataset.xlsx"
)


def calculate_distance_miles(lat1, lon1, lat2, lon2):
    radius_miles = 3958.8

    lat1 = radians(float(lat1))
    lon1 = radians(float(lon1))
    lat2 = radians(float(lat2))
    lon2 = radians(float(lon2))

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return round(radius_miles * c, 2)


def load_providers():
    providers = pd.read_excel(
        DATA_PATH,
        sheet_name="Providers"
    )

    providers.columns = (
        providers.columns
        .str.lower()
        .str.strip()
    )

    return providers


def find_matching_providers(
    predicted_specialty,
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5
):
    providers = load_providers()

    if "specialty" not in providers.columns:
        raise ValueError("Providers sheet must contain a 'specialty' column.")

    if "city" not in providers.columns:
        raise ValueError("Providers sheet must contain a 'city' column.")

    predicted_specialty = str(predicted_specialty).lower().strip()
    patient_city = str(patient_city).lower().strip()

    providers["specialty_clean"] = (
        providers["specialty"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    providers["city_clean"] = (
        providers["city"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    specialty_matches = providers[
        providers["specialty_clean"].str.contains(
            predicted_specialty,
            na=False,
            regex=False
        )
    ].copy()

    if specialty_matches.empty:
        first_word = predicted_specialty.split()[0]

        specialty_matches = providers[
            providers["specialty_clean"].str.contains(
                first_word,
                na=False,
                regex=False
            )
        ].copy()

    if specialty_matches.empty:
        return pd.DataFrame()

    city_matches = specialty_matches[
        specialty_matches["city_clean"] == patient_city
    ].copy()

    if not city_matches.empty:
        final_matches = city_matches
    else:
        final_matches = specialty_matches

    if final_matches.empty:
        return pd.DataFrame()

    final_matches["distance_miles"] = "Unknown"

    has_coordinates = (
        patient_latitude is not None
        and patient_longitude is not None
        and "latitude" in final_matches.columns
        and "longitude" in final_matches.columns
    )

    if has_coordinates:
        final_matches = final_matches.dropna(
            subset=["latitude", "longitude"]
        ).copy()

        if final_matches.empty:
            return pd.DataFrame()

        final_matches["distance_miles"] = final_matches.apply(
            lambda row: calculate_distance_miles(
                patient_latitude,
                patient_longitude,
                row["latitude"],
                row["longitude"]
            ),
            axis=1
        )

        final_matches = final_matches.sort_values(
            by="distance_miles",
            ascending=True
        )

    return final_matches.head(top_n)


if __name__ == "__main__":
    results = find_matching_providers(
        predicted_specialty="Cardiology",
        patient_city="Kansas City",
        patient_latitude=39.0997,
        patient_longitude=-94.5786,
        top_n=5
    )

    if results.empty:
        print("No matching providers found.")
    else:
        print("\nClosest Matching Providers:")
        print(results)