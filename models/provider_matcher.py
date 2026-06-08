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
        and str(lat).lower() != "nan"
        and str(lon).lower() != "nan"
    )


def get_city_coordinates(patient_city):
    providers = load_providers()

    if (
        "city" in providers.columns
        and "latitude" in providers.columns
        and "longitude" in providers.columns
    ):
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

        if not city_matches.empty:
            latitude = city_matches["latitude"].astype(float).mean()
            longitude = city_matches["longitude"].astype(float).mean()
            return latitude, longitude

    missouri_city_coordinates = {
        "kansas city": (39.0997, -94.5786),
        "st louis": (38.6270, -90.1994),
        "saint louis": (38.6270, -90.1994),
        "springfield": (37.2089, -93.2923),
        "columbia": (38.9517, -92.3341),
        "jefferson city": (38.5767, -92.1735),
        "joplin": (37.0842, -94.5133),
        "cape girardeau": (37.3059, -89.5181),
        "kirksville": (40.1948, -92.5832),
        "sedalia": (38.7045, -93.2283),
        "branson": (36.6437, -93.2185),
        "rolla": (37.9514, -91.7713),
        "hannibal": (39.7084, -91.3585),
        "poplar bluff": (36.7570, -90.3929),
        "farmington": (37.7809, -90.4218),
        "west plains": (36.7281, -91.8524),
        "lebanon": (37.6806, -92.6638),
        "maryville": (40.3461, -94.8725),
        "warrensburg": (38.7628, -93.7361),
        "marshall": (39.1231, -93.1969),
        "moberly": (39.4184, -92.4382),
        "mexico": (39.1698, -91.8829),
        "nevada": (37.8392, -94.3547),
        "sikeston": (36.8767, -89.5879),
        "kennett": (36.2362, -90.0556),
        "camdenton": (38.0081, -92.7446)
    }

    city_key = str(patient_city).lower().strip()

    if city_key in missouri_city_coordinates:
        return missouri_city_coordinates[city_key]

    return None, None


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

    if "source" in providers.columns:
        clinics = providers[
            providers["source"]
            .astype(str)
            .str.lower()
            .str.contains("rural health clinics", na=False)
        ].copy()
    else:
        clinics = pd.DataFrame()

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

    if "distance_miles" in clinics.columns:
        if not clinics["distance_miles"].astype(str).eq("Unknown").all():
            clinics = clinics.sort_values(
                by="distance_miles",
                ascending=True
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

    if "distance_miles" in hospitals_or_clinics.columns:
        if not hospitals_or_clinics["distance_miles"].astype(str).eq("Unknown").all():
            hospitals_or_clinics = hospitals_or_clinics.sort_values(
                by="distance_miles",
                ascending=True
            )

    return hospitals_or_clinics.head(top_n)


if __name__ == "__main__":
    test_cities = [
        "Kansas City",
        "Springfield",
        "Columbia",
        "West Plains"
    ]

    for city in test_cities:
        print(f"\nNearest clinics for {city}:")

        results = find_nearest_clinics(
            patient_city=city,
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
