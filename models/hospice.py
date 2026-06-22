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
    if pd.isna(value):
        return ""
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

    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
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
        "cms_certification_number_(ccn)": "facility_id",
        "cms_certification_number": "facility_id",
        "ccn": "facility_id",
        "address_line_1": "address",
        "city": "city_town",
        "zip": "zip_code",
        "county": "county_parish",
        "measure_code": "measure_id",
        "telephone": "telephone_number",
        "phone": "telephone_number",
        "measure_score": "score",
        "value": "score",
        "rate": "score"
    }

    for old_col, new_col in rename_map.items():
        if old_col in df.columns and new_col not in df.columns:
            df = df.rename(columns={old_col: new_col})

    return df


def load_hospice():
    hospice = pd.read_excel(DATA_PATH, sheet_name="Hospice")
    return normalize_columns(hospice)


def load_hospice_condition_map():
    mapping_df = pd.read_excel(DATA_PATH, sheet_name="Hospice_Condition_Map")
    return normalize_columns(mapping_df)


def get_condition_measure_ids(condition):
    condition = clean_text(condition)

    default_codes = [
        "H_012_00_OBSERVED",
        "H_008_01_OBSERVED",
        "H_011_01_OBSERVED"
    ]

    try:
        mapping_df = load_hospice_condition_map()
    except Exception:
        return default_codes

    if "condition_keyword" not in mapping_df.columns or "measure_id" not in mapping_df.columns:
        return default_codes

    mapping_df["condition_clean"] = (
        mapping_df["condition_keyword"]
        .astype(str)
        .str.lower()
        .str.strip()
    )

    exact = mapping_df[mapping_df["condition_clean"] == condition]

    if not exact.empty:
        return exact["measure_id"].dropna().astype(str).str.strip().unique().tolist()

    close = get_close_matches(
        condition,
        mapping_df["condition_clean"].dropna().unique().tolist(),
        n=1,
        cutoff=0.72
    )

    if close:
        matched = mapping_df[mapping_df["condition_clean"] == close[0]]
        return matched["measure_id"].dropna().astype(str).str.strip().unique().tolist()

    keyword_matches = mapping_df[
        mapping_df["condition_clean"].apply(
            lambda keyword: keyword in condition or condition in keyword
        )
    ]

    if not keyword_matches.empty:
        return keyword_matches["measure_id"].dropna().astype(str).str.strip().unique().tolist()

    return default_codes


def get_hospice_coordinates(city):
    city_clean_value = clean_city(city)

    if city_clean_value in FALLBACK_CITY_COORDINATES:
        return FALLBACK_CITY_COORDINATES[city_clean_value]

    close = get_close_matches(
        city_clean_value,
        list(FALLBACK_CITY_COORDINATES.keys()),
        n=1,
        cutoff=0.78
    )

    if close:
        return FALLBACK_CITY_COORDINATES[close[0]]

    return None, None


def add_hospice_distance(hospices, patient_city):
    hospices = hospices.copy()
    patient_lat, patient_lon = get_hospice_coordinates(patient_city)

    if not has_valid_location(patient_lat, patient_lon):
        hospices["distance_miles"] = "Unknown"
        return hospices

    if "city_town" not in hospices.columns:
        hospices["distance_miles"] = "Unknown"
        return hospices

    def row_distance(row):
        hospice_city = row.get("city_town", "")
        hospice_lat, hospice_lon = get_hospice_coordinates(hospice_city)

        if not has_valid_location(hospice_lat, hospice_lon):
            return "Unknown"

        return calculate_distance_miles(
            patient_lat,
            patient_lon,
            hospice_lat,
            hospice_lon
        )

    hospices["distance_miles"] = hospices.apply(row_distance, axis=1)

    known = hospices[hospices["distance_miles"].astype(str) != "Unknown"].copy()
    unknown = hospices[hospices["distance_miles"].astype(str) == "Unknown"].copy()

    if not known.empty:
        known["distance_miles"] = known["distance_miles"].astype(float)
        known = known.sort_values("distance_miles")

    return pd.concat([known, unknown], ignore_index=True)


def filter_by_radius(hospices, radius_miles=60):
    if hospices.empty or "distance_miles" not in hospices.columns:
        return hospices

    known = hospices[hospices["distance_miles"].astype(str) != "Unknown"].copy()
    unknown = hospices[hospices["distance_miles"].astype(str) == "Unknown"].copy()

    if known.empty:
        return hospices

    known["distance_miles"] = known["distance_miles"].astype(float)
    nearby = known[known["distance_miles"] <= radius_miles].copy()

    if nearby.empty:
        return pd.concat([known.head(5), unknown], ignore_index=True)

    return pd.concat([nearby, unknown], ignore_index=True)


def get_numeric_score(row):
    for col in ["score", "measure_score", "value", "rate", "observed_rate"]:
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


def is_sample_size_measure(measure_id):
    measure_id = str(measure_id).upper()
    return any(keyword in measure_id for keyword in ["DENOMINATOR", "AVERAGE_DAILY_CENSUS"])


def is_yes_no_measure(measure_id):
    measure_id = str(measure_id).upper()
    return any(
        keyword in measure_id
        for keyword in ["PROVIDED_HOME_CARE", "BENE_MA_PCT", "BENE_DUAL_PCT"]
    )


def is_higher_better_measure(measure_id):
    measure_id = str(measure_id).upper()

    higher_better_keywords = [
        "OBSERVED",
        "PERCENTILE",
        "PCT_PTS",
        "CARE_PROVIDED"
    ]

    return any(keyword in measure_id for keyword in higher_better_keywords)


def score_hospice_quality(row):
    measure_id = str(row.get("measure_id", "")).upper()
    raw_score = str(row.get("score", "")).strip().lower()
    numeric_score = get_numeric_score(row)

    if is_yes_no_measure(measure_id):
        if raw_score == "yes":
            return 20
        if raw_score == "no":
            return 0

    if numeric_score is None:
        return 0

    if is_sample_size_measure(measure_id):
        if numeric_score >= 500:
            return 10
        if numeric_score >= 100:
            return 7
        if numeric_score >= 30:
            return 4
        if numeric_score >= 10:
            return 2
        return 0

    if is_higher_better_measure(measure_id):
        if numeric_score >= 95:
            return 40
        if numeric_score >= 90:
            return 34
        if numeric_score >= 80:
            return 26
        if numeric_score >= 70:
            return 18
        if numeric_score >= 50:
            return 10
        return 4

    return 0


def add_careconnect_score(hospices):
    hospices = hospices.copy()

    hospices["quality_points"] = hospices.apply(score_hospice_quality, axis=1)

    if "distance_miles" in hospices.columns:
        hospices["distance_points"] = hospices["distance_miles"].apply(
            lambda value:
                0
                if str(value) == "Unknown"
                else max(0, 30 - (float(value) * 0.5))
        )
    else:
        hospices["distance_points"] = 0

    hospices["careconnect_score"] = (
        hospices["quality_points"]
        + hospices["distance_points"]
    )

    return hospices


def summarize_hospices(hospices, top_n=5):
    if hospices.empty:
        return pd.DataFrame()

    hospices = hospices.copy()

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
        if col in hospices.columns
    ]

    if not available_group_columns:
        return hospices.sort_values(
            "careconnect_score",
            ascending=False
        ).head(top_n)

    agg_dict = {}

    if "distance_miles" in hospices.columns:
        agg_dict["distance_miles"] = "first"

    if "measure_id" in hospices.columns:
        agg_dict["measure_id"] = lambda x: ", ".join(
            x.astype(str).drop_duplicates().head(5)
        )

    if "measure_name" in hospices.columns:
        agg_dict["measure_name"] = lambda x: " | ".join(
            x.astype(str).drop_duplicates().head(3)
        )

    if "score" in hospices.columns:
        agg_dict["score"] = lambda x: " | ".join(
            x.astype(str).drop_duplicates().head(5)
        )

    agg_dict["quality_points"] = "sum"
    agg_dict["distance_points"] = "first"
    agg_dict["careconnect_score"] = "sum"

    summary = (
        hospices
        .sort_values("careconnect_score", ascending=False)
        .groupby(available_group_columns, as_index=False)
        .agg(agg_dict)
    )

    count_df = (
        hospices
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


def find_best_hospices(patient_city, condition, top_n=5, radius_miles=60):
    hospices = load_hospice()

    if hospices.empty or "measure_id" not in hospices.columns:
        return pd.DataFrame()

    if "city_town" not in hospices.columns and "city" in hospices.columns:
        hospices["city_town"] = hospices["city"]

    measure_ids = get_condition_measure_ids(condition)
    measure_ids_upper = [
        str(measure_id).upper()
        for measure_id in measure_ids
    ]

    hospices["measure_id_clean"] = (
        hospices["measure_id"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    matches = hospices[
        hospices["measure_id_clean"].isin(measure_ids_upper)
    ].copy()

    if matches.empty:
        default_codes = [
            "H_012_00_OBSERVED",
            "H_008_01_OBSERVED",
            "H_011_01_OBSERVED"
        ]

        matches = hospices[
            hospices["measure_id_clean"].isin(default_codes)
        ].copy()

    if matches.empty:
        return pd.DataFrame()

    matches = add_hospice_distance(
        matches,
        patient_city=patient_city
    )

    matches = filter_by_radius(
        matches,
        radius_miles=radius_miles
    )

    matches = add_careconnect_score(matches)

    return summarize_hospices(
        matches,
        top_n=top_n
    ).head(top_n)


if __name__ == "__main__":
    test_cases = [
        ("Springfield", "advanced cancer"),
        ("Kansas City", "comfort care"),
        ("Houston", "shortness of breath"),
        ("St Louis", "hospice")
    ]

    for city, condition in test_cases:
        print(f"\nBest hospices for {condition} near {city}:")

        results = find_best_hospices(
            patient_city=city,
            condition=condition,
            top_n=5,
            radius_miles=80
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