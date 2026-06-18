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


def normalize_columns(df):
    df = df.copy()

    df.columns = (
        df.columns
        .str.lower()
        .str.strip()
        .str.replace(" ", "_")
        .str.replace("/", "_")
        .str.replace("-", "_")
    )

    rename_map = {
        "measure_code": "measure_id",
        "code": "measure_id",
        "hospital_name": "facility_name",
        "provider_name": "facility_name",
        "phone": "telephone_number",
        "telephone": "telephone_number",
        "city": "city_town",
        "zip": "zip_code",
        "county": "county_parish",
        "measure_score": "score",
        "value": "score",
        "rate": "score"
    }

    for old_col, new_col in rename_map.items():
        if old_col in df.columns and new_col not in df.columns:
            df = df.rename(columns={old_col: new_col})

    return df


def load_long_term():
    long_term = pd.read_excel(
        DATA_PATH,
        sheet_name="Long_Term"
    )

    long_term = normalize_columns(long_term)

    return long_term


def get_condition_measure_ids(condition):
    condition = clean_text(condition)

    mapping = {
        "ventilator": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_011_05_OBS_CHG_MOBL_SCORE"
        ],
        "mobility": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_011_05_OBS_CHG_MOBL_SCORE"
        ],
        "trouble moving": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_011_05_OBS_CHG_MOBL_SCORE"
        ],
        "walking": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_011_05_OBS_CHG_MOBL_SCORE"
        ],
        "rehab": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_011_05_OBS_CHG_MOBL_SCORE"
        ],

        "fall": [
            "L_012_01_OBS_RATE",
            "L_012_01_NUMERATOR"
        ],
        "falls": [
            "L_012_01_OBS_RATE",
            "L_012_01_NUMERATOR"
        ],
        "fall injury": [
            "L_012_01_OBS_RATE",
            "L_012_01_NUMERATOR"
        ],
        "injury": [
            "L_012_01_OBS_RATE",
            "L_012_01_NUMERATOR"
        ],

        "infection": [
            "L_014_01_SIR",
            "L_007_01_SIR"
        ],
        "c diff": [
            "L_014_01_SIR"
        ],
        "c. diff": [
            "L_014_01_SIR"
        ],
        "clostridium difficile": [
            "L_014_01_SIR"
        ],
        "clostridium difficle": [
            "L_014_01_SIR"
        ],
        "blood infection": [
            "L_007_01_SIR"
        ],
        "central line infection": [
            "L_007_01_SIR"
        ],
        "clabsi": [
            "L_007_01_SIR"
        ],

        "long term care": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_012_01_OBS_RATE",
            "L_014_01_SIR",
            "L_007_01_SIR"
        ],
        "long-term care": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_012_01_OBS_RATE",
            "L_014_01_SIR",
            "L_007_01_SIR"
        ],
        "ltch": [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_012_01_OBS_RATE",
            "L_014_01_SIR",
            "L_007_01_SIR"
        ]
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

    for keyword, measure_ids in mapping.items():
        if keyword in condition or condition in keyword:
            return measure_ids

    return [
        "L_011_05_ADJ_CHG_MOBL_SCORE",
        "L_012_01_OBS_RATE",
        "L_014_01_SIR",
        "L_007_01_SIR"
    ]


def get_hospital_coordinates(city):
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

    patient_lat, patient_lon = get_hospital_coordinates(patient_city)

    if not has_valid_location(patient_lat, patient_lon):
        hospitals["distance_miles"] = "Unknown"
        return hospitals

    if "city_town" not in hospitals.columns:
        hospitals["distance_miles"] = "Unknown"
        return hospitals

    def row_distance(row):
        hospital_city = row.get("city_town", "")
        hospital_lat, hospital_lon = get_hospital_coordinates(hospital_city)

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


def filter_by_radius(hospitals, radius_miles=60):
    if hospitals.empty:
        return hospitals

    if "distance_miles" not in hospitals.columns:
        return hospitals

    known = hospitals[
        hospitals["distance_miles"].astype(str) != "Unknown"
    ].copy()

    unknown = hospitals[
        hospitals["distance_miles"].astype(str) == "Unknown"
    ].copy()

    if known.empty:
        return hospitals

    known["distance_miles"] = known["distance_miles"].astype(float)

    nearby = known[
        known["distance_miles"] <= radius_miles
    ].copy()

    if nearby.empty:
        return pd.concat([known.head(5), unknown], ignore_index=True)

    return pd.concat([nearby, unknown], ignore_index=True)


def get_numeric_score(row):
    possible_score_columns = [
        "score",
        "measure_score",
        "value",
        "rate",
        "observed_rate",
        "sir"
    ]

    for col in possible_score_columns:
        if col in row.index:
            value = row.get(col)

            try:
                if pd.isna(value):
                    continue

                value_text = str(value).replace("%", "").replace(",", "").strip()

                if value_text == "" or value_text.lower() in ["not available", "nan", "none"]:
                    continue

                return float(value_text)

            except Exception:
                continue

    return None


def is_lower_better_measure(measure_id):
    measure_id = str(measure_id).upper()

    lower_better_keywords = [
        "SIR",
        "OBS_RATE",
        "NUMERATOR",
        "FALL",
        "INFECTION"
    ]

    return any(keyword in measure_id for keyword in lower_better_keywords)


def is_higher_better_measure(measure_id):
    measure_id = str(measure_id).upper()

    higher_better_keywords = [
        "ADJ_CHG_MOBL_SCORE",
        "OBS_CHG_MOBL_SCORE",
        "MOBL_SCORE"
    ]

    return any(keyword in measure_id for keyword in higher_better_keywords)


def is_sample_size_measure(measure_id):
    measure_id = str(measure_id).upper()

    sample_size_keywords = [
        "DENOMINATOR",
        "ELIGCASES",
        "DOPC_DAYS"
    ]

    return any(keyword in measure_id for keyword in sample_size_keywords)


def score_long_term_quality(row):
    measure_id = str(row.get("measure_id", "")).upper()
    numeric_score = get_numeric_score(row)
    compared = clean_text(row.get("compared_to_national", ""))

    quality_points = 0

    if "better" in compared or "fewer" in compared:
        quality_points += 30
    elif "no different" in compared or "average" in compared:
        quality_points += 15
    elif "worse" in compared or "more" in compared:
        quality_points -= 20

    if numeric_score is None:
        return quality_points

    if is_higher_better_measure(measure_id):
        if numeric_score >= 20:
            quality_points += 40
        elif numeric_score >= 15:
            quality_points += 32
        elif numeric_score >= 10:
            quality_points += 24
        elif numeric_score >= 5:
            quality_points += 14
        else:
            quality_points += 5

    elif is_lower_better_measure(measure_id):
        if numeric_score == 0:
            quality_points += 40
        elif numeric_score <= 0.5:
            quality_points += 36
        elif numeric_score <= 1:
            quality_points += 30
        elif numeric_score <= 2:
            quality_points += 22
        elif numeric_score <= 5:
            quality_points += 12
        else:
            quality_points -= 5

    elif is_sample_size_measure(measure_id):
        if numeric_score >= 100:
            quality_points += 10
        elif numeric_score >= 30:
            quality_points += 6
        elif numeric_score >= 10:
            quality_points += 3

    return quality_points


def add_careconnect_score(hospitals):
    hospitals = hospitals.copy()

    hospitals["quality_points"] = hospitals.apply(
        score_long_term_quality,
        axis=1
    )

    if "distance_miles" in hospitals.columns:
        hospitals["distance_points"] = hospitals["distance_miles"].apply(
            lambda value:
                0
                if str(value) == "Unknown"
                else max(0, 30 - (float(value) * 0.5))
        )
    else:
        hospitals["distance_points"] = 0

    hospitals["careconnect_score"] = (
        hospitals["quality_points"]
        + hospitals["distance_points"]
    )

    return hospitals


def summarize_long_term_hospitals(hospitals, top_n=5):
    if hospitals.empty:
        return pd.DataFrame()

    hospitals = hospitals.copy()

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

    if not available_group_columns:
        return hospitals.sort_values(
            "careconnect_score",
            ascending=False
        ).head(top_n)

    agg_dict = {}

    if "distance_miles" in hospitals.columns:
        agg_dict["distance_miles"] = "first"

    if "measure_id" in hospitals.columns:
        agg_dict["measure_id"] = lambda x: ", ".join(
            x.astype(str).drop_duplicates().head(5)
        )

    if "measure_name" in hospitals.columns:
        agg_dict["measure_name"] = lambda x: " | ".join(
            x.astype(str).drop_duplicates().head(3)
        )

    if "score" in hospitals.columns:
        agg_dict["score"] = lambda x: " | ".join(
            x.astype(str).drop_duplicates().head(5)
        )

    if "compared_to_national" in hospitals.columns:
        agg_dict["compared_to_national"] = lambda x: " | ".join(
            x.astype(str).drop_duplicates().head(3)
        )

    agg_dict["quality_points"] = "sum"
    agg_dict["distance_points"] = "first"
    agg_dict["careconnect_score"] = "sum"

    summary = (
        hospitals
        .sort_values("careconnect_score", ascending=False)
        .groupby(available_group_columns, as_index=False)
        .agg(agg_dict)
    )

    count_df = (
        hospitals
        .groupby(available_group_columns, as_index=False)
        .size()
        .rename(columns={"size": "matched_measure_count"})
    )

    summary = summary.merge(
        count_df,
        on=available_group_columns,
        how="left"
    )

    summary = summary.sort_values(
        ["careconnect_score", "matched_measure_count"],
        ascending=[False, False]
    )

    return summary.head(top_n)


def find_best_long_term_hospitals(
    patient_city,
    condition,
    top_n=5,
    radius_miles=60
):
    long_term = load_long_term()

    if long_term.empty:
        return pd.DataFrame()

    if "measure_id" not in long_term.columns:
        return pd.DataFrame()

    if "city_town" not in long_term.columns and "city" in long_term.columns:
        long_term["city_town"] = long_term["city"]

    measure_ids = get_condition_measure_ids(condition)

    measure_ids_upper = [
        str(measure_id).upper()
        for measure_id in measure_ids
    ]

    long_term["measure_id_clean"] = (
        long_term["measure_id"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    matches = long_term[
        long_term["measure_id_clean"].isin(measure_ids_upper)
    ].copy()

    if matches.empty:
        default_codes = [
            "L_011_05_ADJ_CHG_MOBL_SCORE",
            "L_012_01_OBS_RATE",
            "L_014_01_SIR",
            "L_007_01_SIR"
        ]

        matches = long_term[
            long_term["measure_id_clean"].isin(default_codes)
        ].copy()

    if matches.empty:
        return pd.DataFrame()

    matches = add_hospital_distance(
        matches,
        patient_city=patient_city
    )

    matches = filter_by_radius(
        matches,
        radius_miles=radius_miles
    )

    matches = add_careconnect_score(matches)

    summary = summarize_long_term_hospitals(
        matches,
        top_n=top_n
    )

    return summary.head(top_n)


if __name__ == "__main__":
    test_cases = [
        ("Bolivar", "ltch"),
        ("Springfield", "infection"),
        ("St Louis", "fall injury"),
        ("Kansas City", "ventilator")
    ]

    for city, condition in test_cases:
        print(f"\nBest long-term hospitals for {condition} near {city}:")

        results = find_best_long_term_hospitals(
            patient_city=city,
            condition=condition,
            top_n=5,
            radius_miles=60
        )

        columns_to_show = [
            col for col in [
                "facility_id",
                "facility_name",
                "city_town",
                "telephone_number",
                "distance_miles",
                "measure_id",
                "measure_name",
                "score",
                "careconnect_score",
                "matched_measure_count"
            ]
            if col in results.columns
        ]

        print(results[columns_to_show])

