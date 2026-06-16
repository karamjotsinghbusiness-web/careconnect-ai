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
    "rolla": (37.9514, -91.7713),
    "sedalia": (38.7045, -93.2283),
    "kirksville": (40.1948, -92.5832),
    "hannibal": (39.7084, -91.3585),
    "west plains": (36.7281, -91.8524),
    "cape girardeau": (37.3059, -89.5181),
    "poplar bluff": (36.7570, -90.3929),
    "farmington": (37.7809, -90.4218),
    "houston": (37.3262, -91.9557),
    "saint joseph": (39.7675, -94.8467),
    "st joseph": (39.7675, -94.8467),
    "saint charles": (38.7881, -90.4974),
    "st charles": (38.7881, -90.4974)
}


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
        "saint joseph": "st joseph",
        "saint charles": "st charles",
        "springfeild": "springfield",
        "kansascity": "kansas city",
        "jeff city": "jefferson city",
        "cape": "cape girardeau"
    }

    return aliases.get(city, city)


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


def has_valid_location(lat, lon):
    return (
        lat is not None
        and lon is not None
        and str(lat).strip() != ""
        and str(lon).strip() != ""
        and str(lat).lower() not in ["none", "null", "nan"]
        and str(lon).lower() not in ["none", "null", "nan"]
    )


def load_hospital_quality():
    hospitals = pd.read_excel(
        DATA_PATH,
        sheet_name="Hospital_Quality"
    )

    hospitals.columns = (
        hospitals.columns
        .str.lower()
        .str.strip()
        .str.replace(" ", "_")
        .str.replace("/", "_")
    )

    return hospitals


def get_condition_measure_ids(condition):
    condition = clean_text(condition)

    mapping = {
        "chest pain": ["READM_30_AMI", "EDAC_30_AMI"],
        "heart attack": ["READM_30_AMI", "EDAC_30_AMI"],
        "heart pain": ["READM_30_AMI", "EDAC_30_AMI"],
        "heart problem": ["READM_30_HF", "EDAC_30_HF"],
        "heart failure": ["READM_30_HF", "EDAC_30_HF"],
        "high blood pressure": ["READM_30_HF", "EDAC_30_HF"],

        "shortness of breath": ["READM_30_COPD", "READM_30_PN", "EDAC_30_PN"],
        "breathing problem": ["READM_30_COPD", "READM_30_PN", "EDAC_30_PN"],
        "copd": ["READM_30_COPD"],
        "pneumonia": ["READM_30_PN", "EDAC_30_PN"],
        "cough": ["READM_30_PN", "EDAC_30_PN"],

        "stomach pain": ["OP_32", "OP_36"],
        "abdominal pain": ["OP_32", "OP_36"],
        "vomiting": ["OP_36"],
        "throwing up": ["OP_36"],
        "nausea": ["OP_36"],
        "diarrhea": ["OP_36"],

        "knee pain": ["READM_30_HIP_KNEE"],
        "hip pain": ["READM_30_HIP_KNEE"],
        "joint pain": ["READM_30_HIP_KNEE"],

        "cancer": ["OP_35_ADM", "OP_35_ED"],
        "chemotherapy": ["OP_35_ADM", "OP_35_ED"]
    }

    if condition in mapping:
        return mapping[condition]

    close = get_close_matches(
        condition,
        list(mapping.keys()),
        n=1,
        cutoff=0.72
    )

    if close:
        return mapping[close[0]]

    return [
        "Hybrid_HWR",
        "READM_30_HF",
        "READM_30_PN"
    ]


def get_hospital_city_coordinates(city):
    city_clean = clean_city(city)

    if city_clean in FALLBACK_CITY_COORDINATES:
        return FALLBACK_CITY_COORDINATES[city_clean]

    close = get_close_matches(
        city_clean,
        list(FALLBACK_CITY_COORDINATES.keys()),
        n=1,
        cutoff=0.78
    )

    if close:
        return FALLBACK_CITY_COORDINATES[close[0]]

    return None, None


def add_hospital_distance(hospitals, patient_city):
    hospitals = hospitals.copy()

    patient_lat, patient_lon = get_hospital_city_coordinates(patient_city)

    if not has_valid_location(patient_lat, patient_lon):
        hospitals["distance_miles"] = "Unknown"
        return hospitals

    if "city_town" not in hospitals.columns:
        hospitals["distance_miles"] = "Unknown"
        return hospitals

    def row_distance(row):
        hospital_city = row.get("city_town", "")
        hospital_lat, hospital_lon = get_hospital_city_coordinates(hospital_city)

        if not has_valid_location(hospital_lat, hospital_lon):
            return "Unknown"

        return calculate_distance_miles(
            patient_lat,
            patient_lon,
            hospital_lat,
            hospital_lon
        )

    hospitals["distance_miles"] = hospitals.apply(row_distance, axis=1)

    known = hospitals[
        hospitals["distance_miles"].astype(str) != "Unknown"
    ].copy()

    unknown = hospitals[
        hospitals["distance_miles"].astype(str) == "Unknown"
    ].copy()

    if not known.empty:
        known["distance_miles"] = known["distance_miles"].astype(float)
        known = known.sort_values("distance_miles")

    return pd.concat([known, unknown], ignore_index=True)


def score_quality(row):
    compared = clean_text(row.get("compared_to_national", ""))
    score = row.get("score", "")

    quality_points = 0

    if "better" in compared or "fewer" in compared:
        quality_points += 30
    elif "no different" in compared or "average" in compared:
        quality_points += 18
    elif "worse" in compared or "more" in compared:
        quality_points -= 15

    try:
        numeric_score = float(score)

        if numeric_score <= 10:
            quality_points += 15
        elif numeric_score <= 15:
            quality_points += 10
        elif numeric_score <= 20:
            quality_points += 5
        else:
            quality_points -= 5

    except Exception:
        pass

    return quality_points


def summarize_hospitals(hospitals, top_n=5):
    if hospitals.empty:
        return pd.DataFrame()

    hospitals = hospitals.copy()

    hospitals["quality_points"] = hospitals.apply(score_quality, axis=1)

    if "distance_miles" in hospitals.columns:
        hospitals["distance_points"] = hospitals["distance_miles"].apply(
            lambda value: 0 if str(value) == "Unknown" else max(0, 30 - float(value))
        )
    else:
        hospitals["distance_points"] = 0

    hospitals["careconnect_score"] = (
        hospitals["quality_points"]
        + hospitals["distance_points"]
    )

    group_columns = [
        "facility_id",
        "facility_name",
        "address",
        "city_town",
        "state",
        "zip_code",
        "county_parish",
        "telephone_number"
    ]

    available_group_columns = [
        col for col in group_columns
        if col in hospitals.columns
    ]

    summary = (
        hospitals
        .sort_values("careconnect_score", ascending=False)
        .groupby(available_group_columns, as_index=False)
        .agg({
            "distance_miles": "first",
            "measure_id": lambda x: ", ".join(x.astype(str).head(3)),
            "measure_name": lambda x: " | ".join(x.astype(str).head(2)),
            "compared_to_national": lambda x: " | ".join(x.astype(str).head(2)),
            "score": lambda x: " | ".join(x.astype(str).head(2)),
            "careconnect_score": "max"
        })
    )

    summary = summary.sort_values(
        by=["careconnect_score", "distance_miles"],
        ascending=[False, True]
    )

    return summary.head(top_n)


def find_best_hospitals(
    patient_city,
    condition,
    top_n=5,
    radius_miles=60
):
    hospitals = load_hospital_quality()

    measure_ids = get_condition_measure_ids(condition)

    if "measure_id" not in hospitals.columns:
        return pd.DataFrame()

    hospitals["measure_id_clean"] = (
        hospitals["measure_id"]
        .astype(str)
        .str.strip()
    )

    matches = hospitals[
        hospitals["measure_id_clean"].isin(measure_ids)
    ].copy()

    if matches.empty:
        matches = hospitals.copy()

    matches = add_hospital_distance(
        matches,
        patient_city=patient_city
    )

    if "distance_miles" in matches.columns:
        known = matches[
            matches["distance_miles"].astype(str) != "Unknown"
        ].copy()

        if not known.empty:
            known["distance_miles"] = known["distance_miles"].astype(float)
            nearby = known[
                known["distance_miles"] <= radius_miles
            ].copy()

            if not nearby.empty:
                matches = nearby
            else:
                matches = known.head(20)

    return summarize_hospitals(matches, top_n=top_n)


if __name__ == "__main__":
    test_cases = [
        ("Springfield", "Chest Pain"),
        ("Joplin", "Chest Pain"),
        ("Rolla", "Pneumonia"),
        ("Bolivar", "Throwing up"),
        ("Kansas City", "Cancer")
    ]

    for city, condition in test_cases:
        print(f"\nBest hospitals near {city} for {condition}:")

        results = find_best_hospitals(
            patient_city=city,
            condition=condition,
            top_n=5,
            radius_miles=60
        )

        columns_to_show = [
            col for col in [
                "facility_name",
                "city_town",
                "telephone_number",
                "distance_miles",
                "measure_id",
                "compared_to_national",
                "score",
                "careconnect_score"
            ]
            if col in results.columns
        ]

        print(results[columns_to_show])
