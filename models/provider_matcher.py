import pandas as pd
from pathlib import Path
from math import radians, sin, cos, sqrt, atan2
from difflib import get_close_matches

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "missouri_healthcare_linked_dataset_with_expanded_symptoms.xlsx"


FALLBACK_CITY_COORDINATES = {
    "springfield": (37.2089, -93.2923),
    "fordland": (37.1573, -92.9410),
    "lebanon": (37.6806, -92.6638),
    "bolivar": (37.6145, -93.4105),
    "st louis": (38.6270, -90.1994),
    "saint louis": (38.6270, -90.1994),
    "kansas city": (39.0997, -94.5786),
    "columbia": (38.9517, -92.3341),
    "jefferson city": (38.5767, -92.1735),
    "joplin": (37.0842, -94.5133),
    "west plains": (36.7281, -91.8524),
    "rolla": (37.9514, -91.7713),
    "marshfield": (37.3387, -92.9071),
    "ozark": (37.0209, -93.2060),
    "nixa": (37.0434, -93.2944),
    "branson": (36.6437, -93.2185),
    "camdenton": (38.0081, -92.7446),
    "farmington": (37.7809, -90.4218),
    "poplar bluff": (36.7570, -90.3929),
    "cape girardeau": (37.3059, -89.5181),
    "hannibal": (39.7084, -91.3585),
    "sedalia": (38.7045, -93.2283),
    "kirksville": (40.1948, -92.5832),
    "maryville": (40.3461, -94.8725),
    "warrensburg": (38.7628, -93.7361),
    "moberly": (39.4184, -92.4382),
    "mexico": (39.1698, -91.8829),
    "nevada": (37.8392, -94.3547),
    "sikeston": (36.8767, -89.5879),
    "kennett": (36.2362, -90.0556),
    "ava": (36.9517, -92.6605),
    "mountain grove": (37.1306, -92.2635),
    "willow springs": (36.9923, -91.9699),
    "houston": (37.3262, -91.9557),
    "cabool": (37.1239, -92.1010),
    "salem": (37.6456, -91.5357),
    "waynesville": (37.8287, -92.2007),
    "buffalo": (37.6439, -93.0924),
    "el dorado springs": (37.8767, -94.0213),
    "clinton": (38.3686, -93.7783),
    "versailles": (38.4314, -92.8410),
    "osage beach": (38.1503, -92.6179),
    "lake ozark": (38.1986, -92.6388),
    "richland": (37.8567, -92.4043),
    "conway": (37.5020, -92.8210),
    "monett": (36.9289, -93.9277),
    "aurora": (36.9709, -93.7179),
    "republic": (37.1201, -93.4802),
    "mount vernon": (37.1037, -93.8185),
    "carthage": (37.1764, -94.3102),
    "webb city": (37.1464, -94.4630),
    "neosho": (36.8689, -94.3679),
    "lamar": (37.4950, -94.2766),
    "stockton": (37.6989, -93.7963),
    "hermitage": (37.9414, -93.3169)
}


def calculate_distance_miles(lat1, lon1, lat2, lon2):
    radius_miles = 3958.8
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])

    lat1 = radians(lat1)
    lon1 = radians(lon1)
    lat2 = radians(lat2)
    lon2 = radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        sin(dlat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    )

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


def clean_text(value):
    return str(value).lower().strip()


def clean_city(value):
    city = clean_text(value)
    city = city.replace(".", "")
    city = city.replace(",", "")
    city = city.replace(" missouri", "")
    city = city.replace(" mo", "")
    city = " ".join(city.split())

    aliases = {
        "saint louis": "st louis",
        "st louis city": "st louis",
        "springfeild": "springfield",
        "kansascity": "kansas city",
        "jeff city": "jefferson city",
        "cape": "cape girardeau"
    }

    return aliases.get(city, city)


def has_valid_location(lat, lon):
    return (
        lat is not None
        and lon is not None
        and str(lat).strip() != ""
        and str(lon).strip() != ""
        and str(lat).lower() not in ["none", "null", "nan"]
        and str(lon).lower() not in ["none", "null", "nan"]
    )


def get_city_coordinates(patient_city):
    providers = load_providers()
    city_input = clean_city(patient_city)

    if (
        "city" in providers.columns
        and "latitude" in providers.columns
        and "longitude" in providers.columns
    ):
        providers["city_clean"] = providers["city"].apply(clean_city)

        city_matches = providers[
            providers["city_clean"] == city_input
        ].copy()

        city_matches = city_matches.dropna(
            subset=["latitude", "longitude"]
        )

        if not city_matches.empty:
            lat = city_matches["latitude"].astype(float).mean()
            lon = city_matches["longitude"].astype(float).mean()
            return lat, lon

        all_cities = (
            providers["city_clean"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        close_city = get_close_matches(
            city_input,
            all_cities,
            n=1,
            cutoff=0.78
        )

        if close_city:
            city_matches = providers[
                providers["city_clean"] == close_city[0]
            ].copy()

            city_matches = city_matches.dropna(
                subset=["latitude", "longitude"]
            )

            if not city_matches.empty:
                lat = city_matches["latitude"].astype(float).mean()
                lon = city_matches["longitude"].astype(float).mean()
                return lat, lon

    if city_input in FALLBACK_CITY_COORDINATES:
        return FALLBACK_CITY_COORDINATES[city_input]

    close_fallback = get_close_matches(
        city_input,
        list(FALLBACK_CITY_COORDINATES.keys()),
        n=1,
        cutoff=0.78
    )

    if close_fallback:
        return FALLBACK_CITY_COORDINATES[close_fallback[0]]

    return None, None


def add_distance(
    df,
    patient_latitude=None,
    patient_longitude=None,
    patient_city=None
):
    df = df.copy()
    patient_latitude, patient_longitude = get_city_coordinates(patient_city)

    if not has_valid_location(patient_latitude, patient_longitude):
        df["distance_miles"] = "Unknown"
        return df

    if "latitude" not in df.columns:
        df["latitude"] = None

    if "longitude" not in df.columns:
        df["longitude"] = None

    def get_row_distance(row):
        row_lat = row.get("latitude")
        row_lon = row.get("longitude")

        if not has_valid_location(row_lat, row_lon):
            row_city = row.get("city", "")
            row_lat, row_lon = get_city_coordinates(row_city)

        if not has_valid_location(row_lat, row_lon):
            return "Unknown"

        return calculate_distance_miles(
            patient_latitude,
            patient_longitude,
            row_lat,
            row_lon
        )

    df["distance_miles"] = df.apply(get_row_distance, axis=1)

    known = df[
        df["distance_miles"].astype(str) != "Unknown"
    ].copy()

    unknown = df[
        df["distance_miles"].astype(str) == "Unknown"
    ].copy()

    if not known.empty:
        known["distance_miles"] = known["distance_miles"].astype(float)
        known = known.sort_values("distance_miles")

    return pd.concat([known, unknown], ignore_index=True)


def filter_by_radius(df, radius_miles=30):
    if df.empty:
        return df

    if "distance_miles" not in df.columns:
        return df

    known_distance = df[
        df["distance_miles"].astype(str) != "Unknown"
    ].copy()

    if known_distance.empty:
        return df

    known_distance["distance_miles"] = (
        known_distance["distance_miles"]
        .astype(float)
    )

    nearby = known_distance[
        known_distance["distance_miles"] <= radius_miles
    ].copy()

    if nearby.empty:
        return known_distance.head(5)

    return nearby


def create_search_text(providers):
    providers = providers.copy()

    providers["search_text"] = (
        safe_text_column(providers, "specialty") + " " +
        safe_text_column(providers, "primary_specialty") + " " +
        safe_text_column(providers, "secondary_specialty") + " " +
        safe_text_column(providers, "provider_type") + " " +
        safe_text_column(providers, "source") + " " +
        safe_text_column(providers, "organization") + " " +
        safe_text_column(providers, "provider_name") + " " +
        safe_text_column(providers, "facility_name") + " " +
        safe_text_column(providers, "clinic_name") + " " +
        safe_text_column(providers, "credential")
    ).str.lower()

    return providers


def get_specialty_search_terms(predicted_specialty):
    specialty = clean_text(predicted_specialty)

    specialty_map = {
        "family practice": [
            "family practice",
            "family medicine",
            "primary care",
            "general practice",
            "internal medicine",
            "nurse practitioner"
        ],
        "cardiovascular disease (cardiology)": [
            "cardiology",
            "cardiovascular",
            "heart",
            "interventional cardiology"
        ],
        "mental health counselor": [
            "mental health counselor",
            "clinical social worker",
            "clinical psychologist",
            "behavioral health",
            "psychology",
            "counselor"
        ],
        "clinical social worker": [
            "clinical social worker",
            "mental health counselor",
            "clinical psychologist",
            "behavioral health"
        ],
        "physical therapist in private practice": [
            "physical therapist",
            "physical therapy",
            "rehabilitation"
        ],
        "occupational therapist in private practice": [
            "occupational therapist",
            "occupational therapy"
        ],
        "qualified speech language pathologist": [
            "speech language pathologist",
            "speech therapy",
            "speech"
        ],
        "pulmonology": [
            "pulmonology",
            "pulmonary",
            "respiratory",
            "pulmonary disease"
        ],
        "pulmonary disease": [
            "pulmonology",
            "pulmonary",
            "respiratory",
            "pulmonary disease"
        ],
        "nephrology": [
            "nephrology",
            "kidney"
        ],
        "neurology": [
            "neurology",
            "neurologist"
        ],
        "infectious disease": [
            "infectious disease"
        ],
        "nurse practitioner": [
            "nurse practitioner",
            "family practice",
            "family medicine",
            "primary care"
        ]
    }

    return specialty_map.get(specialty, [specialty])


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
        clinics["provider_name"] = clinics["clinic_name"]

    clinics["specialty"] = "Rural Health Clinic"

    return clinics


def find_matching_providers(
    predicted_specialty,
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5,
    radius_miles=30
):
    providers = load_providers()

    if "specialty" not in providers.columns:
        return pd.DataFrame()

    providers = create_search_text(providers)

    search_terms = get_specialty_search_terms(predicted_specialty)

    matches = providers[
        providers["search_text"].apply(
            lambda value: any(term in value for term in search_terms)
        )
    ].copy()

    if matches.empty:
        predicted_specialty_clean = clean_text(predicted_specialty)

        matches = providers[
            providers["search_text"].str.contains(
                predicted_specialty_clean,
                na=False,
                regex=False
            )
        ].copy()

    if matches.empty:
        return pd.DataFrame()

    matches = add_distance(
        matches,
        patient_city=patient_city
    )

    matches = filter_by_radius(
        matches,
        radius_miles=radius_miles
    )

    return matches.head(top_n)


def find_nearest_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5,
    radius_miles=30
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
        patient_city=patient_city
    )

    clinics = filter_by_radius(
        clinics,
        radius_miles=radius_miles
    )

    return clinics.head(top_n)


def find_nearest_hospitals_or_clinics(
    patient_city,
    patient_latitude=None,
    patient_longitude=None,
    top_n=5,
    radius_miles=30
):
    providers = load_providers()
    providers = create_search_text(providers)

    include_terms = [
        "hospital",
        "medical center",
        "health center",
        "rural health clinic",
        "urgent care",
        "walk in clinic",
        "walk-in clinic",
        "family medical",
        "family clinic",
        "community health",
        "clinic"
    ]

    exclude_terms = [
        "clinical social worker",
        "mental health counselor",
        "clinical psychologist",
        "psychologist",
        "counselor",
        "physical therapist",
        "occupational therapist",
        "speech language pathologist",
        "speech therapist",
        "dietitian",
        "nutrition",
        "diagnostic radiology",
        "radiology",
        "independent practice"
    ]

    hospitals_or_clinics = providers[
        providers["search_text"].apply(
            lambda value:
                any(term in value for term in include_terms)
                and not any(term in value for term in exclude_terms)
        )
    ].copy()

    if hospitals_or_clinics.empty:
        hospitals_or_clinics = find_nearest_clinics(
            patient_city=patient_city,
            patient_latitude=patient_latitude,
            patient_longitude=patient_longitude,
            top_n=top_n,
            radius_miles=radius_miles
        )

        return hospitals_or_clinics

    hospitals_or_clinics = add_distance(
        hospitals_or_clinics,
        patient_city=patient_city
    )

    hospitals_or_clinics = filter_by_radius(
        hospitals_or_clinics,
        radius_miles=radius_miles
    )

    return hospitals_or_clinics.head(top_n)


if __name__ == "__main__":
    test_cases = [
        ("Springfield", "Family Practice"),
        ("Bolivar", "Family Practice"),
        ("Lebanon", "Family Practice"),
        ("Springfield", "Cardiovascular Disease (Cardiology)")
    ]

    for city, specialty in test_cases:
        print(f"\nProviders for {specialty} in/near {city}:")

        results = find_matching_providers(
            predicted_specialty=specialty,
            patient_city=city,
            top_n=10,
            radius_miles=30
        )

        columns_to_show = [
            col for col in [
                "provider_id",
                "provider_name",
                "specialty",
                "primary_specialty",
                "organization",
                "city",
                "phone",
                "distance_miles",
                "source"
            ]
            if col in results.columns
        ]

        print(results[columns_to_show])

    print("\nFallback hospitals/clinics near Springfield:")

    fallback_results = find_nearest_hospitals_or_clinics(
        patient_city="Bolivar",
        top_n=10,
        radius_miles=30
    )

    fallback_columns = [
        col for col in [
            "provider_id",
            "provider_name",
            "clinic_name",
            "specialty",
            "primary_specialty",
            "organization",
            "city",
            "phone",
            "distance_miles",
            "source"
        ]
        if col in fallback_results.columns
    ]

    print(fallback_results[fallback_columns])