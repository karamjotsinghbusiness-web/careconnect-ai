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


def safe_text_column(df, column_name):
    if column_name in df.columns:
        return df[column_name].astype(str)

    return pd.Series([""] * len(df), index=df.index)


def has_valid_location(lat, lon):
    return (
        lat is not None
        and lon is not None
        and str(lat).strip() != ""
        and str(lon).strip() != ""
        and str(lat).lower() != "none"
        and str(lon).lower() != "none"
        and str(lat).lower() != "null"
        and str(lon).lower() != "null"
    )


def get_city_coordinates(patient_city):
    providers = load_providers()

    if "city" not in providers.columns:
        return None, None

    if "latitude" not in providers.columns or "longitude" not in providers.columns:
        return None, None

    city_matches = providers[
        providers["city"]
        .astype(str)
        .str.lower()
        .str.strip()
        == str(patient_city).lower().strip()
    ].copy()

    city_matches = city_matches.dropna(
        subset=["latitude", "longitude"]
    )

    if city_matches.empty:
        return None, None

    latitude = city_matches["latitude"].astype(float).mean()
    longitude = city_matches["longitude"].astype(float).mean()

    return latitude, longitude


def add_distance(
    df,
    patient_latitude=None,
    patient_longitude=None,
    patient_city=None
):
    df = df.copy()

    if not has_valid_location(patient_latitude, patient_longitude):
        patient_latitude, patient_longitude = get_city_coordinates(patient_city)

    has_patient_location = has_valid_location(
        patient_latitude,
        patient_longitude
    )

    has_provider_location = (
        "latitude" in df.columns
        and "longitude" in df.columns
    )

    if has_patient_location and has_provider_location:
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

        df = df.sort_values(
            by="distance_miles",
            ascending=True
        )

    else:
        df["distance_miles"] = "Unknown"

    return df


def create_search_text(providers):
    providers = providers.copy()

    providers["search_text"] = (
        safe_text_column(providers, "specialty") + " " +
        safe_text_column(providers, "source") + " " +
        safe_text_column(providers, "organization") + " " +
        safe_text_column(providers, "provider_name") + " " +
        safe_text_column(providers, "facility_name") + " " +
        safe_text_column(providers, "clinic_name") + " " +
        safe_text_column(providers, "provider_type")
    ).str.lower()

    return providers


def clean_rural_clinic_rows(clinics):
    clinics = clinics.copy()

    if "clinic_name" not in clinics.columns:
        clinics["clinic_name"] = ""

    if "facility_name" in clinics.columns:
        clinics["clinic_name"] = clinics["clinic_name"].fillna("")
        clinics.loc[
            clinics["clinic_name"].astype(str).str.strip() == "",
            "clinic_name"
        ] = clinics["facility_name"]

    if "organization" in clinics.columns:
        clinics["clinic_name"] = clinics["clinic_name"].fillna("")
        clinics.loc[
            clinics["clinic_name"].astype(str).str.strip() == "",
            "clinic_name"
        ] = clinics["organization"]

    if "provider_name" in clinics.columns:
        clinics.loc[:, "provider_name"] = clinics["clinic_name"]

    clinics.loc[:, "specialty"] = "Rural Health Clinic"

    return clinics


def find_matching_providers(
    predicted_specialty,
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5
):
    providers = load_providers()

    if "specialty" not in providers.columns:
        return pd.DataFrame()

    predicted_specialty = str(predicted_specialty).lower().strip()
    patient_city = str(patient_city).lower().strip()

    providers["specialty_clean"] = (
        providers["specialty"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    if "city" in providers.columns:
        providers["city_clean"] = (
            providers["city"]
            .astype(str)
            .str.lower()
            .str.strip()
        )
    else:
        providers["city_clean"] = ""

    matches = providers[
        providers["specialty_clean"].str.contains(
            predicted_specialty,
            na=False,
            regex=False
        )
    ].copy()

    if matches.empty and predicted_specialty:
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

    city_matches = matches[
        matches["city_clean"] == patient_city
    ].copy()

    if not city_matches.empty:
        matches = city_matches

    matches = add_distance(
        matches,
        patient_latitude,
        patient_longitude,
        patient_city
    )

    return matches.head(top_n)


def find_nearest_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=3
):
    providers = load_providers()

    if "source" not in providers.columns:
        return pd.DataFrame()

    clinics = providers[
        providers["source"]
        .astype(str)
        .str.lower()
        .str.contains("rural health clinics", na=False)
    ].copy()

    if clinics.empty:
        providers = create_search_text(providers)

        clinics = providers[
            providers["search_text"].str.contains(
                "rural health clinic",
                na=False,
                regex=False
            )
        ].copy()

    if clinics.empty:
        return pd.DataFrame()

    clinics = clean_rural_clinic_rows(clinics)

    clinics = add_distance(
        clinics,
        patient_latitude,
        patient_longitude,
        patient_city
    )

    return clinics.head(top_n)


def find_nearest_hospitals_or_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5
):
    providers = load_providers()
    providers = create_search_text(providers)

    hospitals_or_clinics = providers[
        providers["search_text"].str.contains(
            "hospital|medical center|health center|rural health clinic|community health",
            na=False,
            regex=True
        )
    ].copy()

    if hospitals_or_clinics.empty:
        hospitals_or_clinics = providers.copy()

    hospitals_or_clinics = add_distance(
        hospitals_or_clinics,
        patient_latitude,
        patient_longitude,
        patient_city
    )

    return hospitals_or_clinics.head(top_n)


if __name__ == "__main__":
    results = find_nearest_clinics(
        patient_city="Kansas City",
        patient_latitude=None,
        patient_longitude=None,
        top_n=5
    )

    columns_to_show = [
        col for col in [
            "provider_id",
            "clinic_name",
            "provider_name",
            "specialty",
            "city",
            "latitude",
            "longitude",
            "distance_miles",
            "source"
        ]
        if col in results.columns
    ]

    print(results[columns_to_show])
