"""Normalize and validate authoritative public provider/facility data.

This module deliberately handles provider discovery data only. It must not be
used to create diagnosis labels, infer insurance network participation, or
claim that a provider is accepting new patients.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone


PROVIDER_COLUMNS = [
    "provider_id",
    "npi",
    "provider_name",
    "gender",
    "credential",
    "primary_specialty",
    "secondary_specialty",
    "specialty",
    "organization",
    "city",
    "state",
    "zip_code",
    "phone",
    "accepting_new_patients",
    "source",
    "address",
    "latitude",
    "longitude",
]

CMS_DATASET_ID = "mj5m-pzi6"
CMS_DATASET_PAGE = (
    "https://data.cms.gov/provider-data/dataset/mj5m-pzi6"
)
CMS_METADATA_URL = (
    "https://data.cms.gov/provider-data/api/1/metastore/"
    "schemas/dataset/items/mj5m-pzi6"
)
CMS_QUERY_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/"
    "query/mj5m-pzi6/0"
)
HRSA_DATASET_PAGE = (
    "https://data.hrsa.gov/data/download?titleFilter=Health+Center"
)
HRSA_CSV_URL = (
    "https://data.hrsa.gov/DataDownload/DD_Files/"
    "Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
)


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value, maximum=None):
    if value is None:
        return ""
    result = re.sub(r"\s+", " ", str(value)).strip()
    if maximum is not None:
        return result[:maximum]
    return result


def digits(value):
    return re.sub(r"\D", "", clean(value))


def normalize_npi(value):
    candidate = digits(value)
    return candidate if is_valid_npi(candidate) else ""


def is_valid_npi(value):
    """Validate an NPI using the CMS check-digit algorithm."""

    candidate = digits(value)
    if len(candidate) != 10:
        return False

    payload = [int(character) for character in f"80840{candidate}"]
    total = 0
    parity = len(payload) % 2
    for index, number in enumerate(payload):
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        total += number
    return total % 10 == 0


def normalize_phone(value):
    candidate = digits(value)
    if len(candidate) == 11 and candidate.startswith("1"):
        candidate = candidate[1:]
    if len(candidate) != 10:
        return ""
    return f"{candidate[:3]}-{candidate[3:6]}-{candidate[6:]}"


def normalize_zip(value):
    candidate = digits(value)
    if len(candidate) >= 9:
        return f"{candidate[:5]}-{candidate[5:9]}"
    return candidate[:5]


def normalize_name(value):
    value = clean(value, 200)
    if value.isupper():
        return value.title()
    return value


def stable_provider_id(prefix, *values):
    identity = "|".join(clean(value).upper() for value in values)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def parse_coordinate(value, minimum, maximum):
    try:
        coordinate = float(clean(value))
    except (TypeError, ValueError):
        return ""
    if not minimum <= coordinate <= maximum:
        return ""
    return round(coordinate, 8)


def _blank_provider():
    return {column: "" for column in PROVIDER_COLUMNS}


def normalize_cms_provider(row, source_modified):
    """Normalize one CMS Care Compare clinician/location record."""

    if clean(row.get("state")).upper() != "MO":
        return None

    npi = normalize_npi(row.get("npi"))
    first = normalize_name(row.get("provider_first_name"))
    middle = normalize_name(row.get("provider_middle_name"))
    last = normalize_name(row.get("provider_last_name"))
    suffix = clean(row.get("suff"), 30)
    provider_name = clean(" ".join(part for part in (first, middle, last, suffix) if part), 200)
    if not npi or not provider_name:
        return None

    secondary = []
    for column in ("sec_spec_1", "sec_spec_2", "sec_spec_3", "sec_spec_4"):
        value = normalize_name(row.get(column))
        if value and value not in secondary:
            secondary.append(value)

    primary_specialty = normalize_name(row.get("pri_spec")) or "Clinician"
    address = clean(
        " ".join(
            part
            for part in (
                clean(row.get("adr_ln_1")),
                clean(row.get("adr_ln_2")),
            )
            if part
        ),
        300,
    )
    provider = _blank_provider()
    provider.update(
        {
            "provider_id": stable_provider_id(
                "cms",
                npi,
                row.get("ind_enrl_id"),
                row.get("org_pac_id"),
                row.get("adrs_id"),
            ),
            "npi": npi,
            "provider_name": provider_name,
            "gender": clean(row.get("gndr"), 30).upper(),
            "credential": clean(row.get("cred"), 50).upper(),
            "primary_specialty": primary_specialty,
            "secondary_specialty": "; ".join(secondary),
            "specialty": primary_specialty,
            "organization": normalize_name(row.get("facility_name")),
            "city": normalize_name(row.get("citytown")),
            "state": "MO",
            "zip_code": normalize_zip(row.get("zip_code")),
            "phone": normalize_phone(row.get("telephone_number")),
            # CMS enrollment/assignment fields do not establish current
            # appointment availability or a patient's insurance network.
            "accepting_new_patients": "Unknown",
            "source": (
                "CMS Care Compare National Downloadable File"
                f" (modified {clean(source_modified) or 'date unavailable'})"
            ),
            "address": address,
        }
    )
    return provider


def normalize_hrsa_site(row, source_record_date=""):
    """Normalize one active Missouri HRSA health-center site."""

    if clean(row.get("Site State Abbreviation")).upper() != "MO":
        return None
    if clean(row.get("Site Status Description")).lower() != "active":
        return None
    site_type = clean(row.get("Health Center Type Description"))
    if site_type.lower() == "administrative":
        return None

    site_name = normalize_name(row.get("Site Name"))
    if not site_name:
        return None

    npi_value = clean(row.get("FQHC Site NPI Number"))
    npi = normalize_npi(npi_value)
    if npi_value and not npi:
        return None

    health_center_type = (
        clean(row.get("Health Center Type"), 150)
        or clean(site_type, 150)
        or "Health Center"
    )
    location_type = clean(
        row.get("Health Center Location Type Description"),
        50,
    )
    if location_type and location_type.lower() != "permanent":
        health_center_type = clean(
            f"{health_center_type} — {location_type}",
            200,
        )
    provider = _blank_provider()
    provider.update(
        {
            "provider_id": stable_provider_id(
                "hrsa",
                row.get("BPHC Assigned Number"),
                row.get("Health Center Number"),
                row.get("Health Center Location Identification Number"),
                site_name,
                row.get("Site Address"),
            ),
            "npi": npi,
            "provider_name": site_name,
            "primary_specialty": health_center_type,
            "specialty": health_center_type,
            "organization": normalize_name(row.get("Health Center Name")),
            "city": normalize_name(row.get("Site City")),
            "state": "MO",
            "zip_code": normalize_zip(row.get("Site Postal Code")),
            "phone": normalize_phone(row.get("Site Telephone Number")),
            "accepting_new_patients": "Unknown",
            "source": (
                "HRSA Health Center Service Delivery and Look-Alike Sites"
                f" (record {clean(source_record_date) or 'date unavailable'})"
            ),
            "address": clean(row.get("Site Address"), 300),
            # HRSA labels X as longitude and Y as latitude.
            "latitude": parse_coordinate(
                row.get("Geocoding Artifact Address Primary Y Coordinate"),
                -90,
                90,
            ),
            "longitude": parse_coordinate(
                row.get("Geocoding Artifact Address Primary X Coordinate"),
                -180,
                180,
            ),
        }
    )
    return provider


def provider_completeness(provider):
    weighted_columns = {
        "address": 3,
        "latitude": 3,
        "longitude": 3,
        "phone": 2,
        "organization": 1,
        "secondary_specialty": 1,
    }
    return sum(
        weight for column, weight in weighted_columns.items() if clean(provider.get(column))
    )


def deduplicate_providers(providers):
    """Keep the most complete record for each stable source identifier."""

    best = {}
    for provider in providers:
        provider_id = clean(provider.get("provider_id"))
        if not provider_id:
            continue
        current = best.get(provider_id)
        if current is None or provider_completeness(provider) > provider_completeness(current):
            best[provider_id] = provider
    return sorted(best.values(), key=lambda row: row["provider_id"])


def validate_providers(providers, minimum_cms=1, minimum_hrsa=1):
    """Return an inspectable quality report and raise nothing."""

    errors = []
    provider_ids = [clean(row.get("provider_id")) for row in providers]
    sources = Counter(
        "CMS" if clean(row.get("source")).startswith("CMS ") else
        "HRSA" if clean(row.get("source")).startswith("HRSA ") else
        "Other"
        for row in providers
    )

    duplicate_ids = len(provider_ids) - len(set(provider_ids))
    if duplicate_ids:
        errors.append(f"{duplicate_ids} duplicate provider_id values")

    missing_names = sum(not clean(row.get("provider_name")) for row in providers)
    if missing_names:
        errors.append(f"{missing_names} rows are missing provider_name")

    wrong_state = sum(clean(row.get("state")).upper() != "MO" for row in providers)
    if wrong_state:
        errors.append(f"{wrong_state} rows are outside Missouri")

    invalid_npis = sum(
        bool(clean(row.get("npi"))) and not is_valid_npi(row.get("npi"))
        for row in providers
    )
    if invalid_npis:
        errors.append(f"{invalid_npis} rows contain invalid NPI check digits")

    invalid_accepting = sum(
        clean(row.get("accepting_new_patients")) != "Unknown"
        for row in providers
    )
    if invalid_accepting:
        errors.append(
            f"{invalid_accepting} public rows make unsupported availability claims"
        )

    invalid_coordinates = 0
    coordinate_rows = 0
    for row in providers:
        latitude = clean(row.get("latitude"))
        longitude = clean(row.get("longitude"))
        if latitude or longitude:
            coordinate_rows += 1
            try:
                valid = (
                    latitude
                    and longitude
                    and -90 <= float(latitude) <= 90
                    and -180 <= float(longitude) <= 180
                )
            except ValueError:
                valid = False
            if not valid:
                invalid_coordinates += 1
    if invalid_coordinates:
        errors.append(f"{invalid_coordinates} rows contain invalid coordinate pairs")

    if sources["CMS"] < minimum_cms:
        errors.append(
            f"CMS row count {sources['CMS']} is below required minimum {minimum_cms}"
        )
    if sources["HRSA"] < minimum_hrsa:
        errors.append(
            f"HRSA row count {sources['HRSA']} is below required minimum {minimum_hrsa}"
        )

    missing_phone = sum(not clean(row.get("phone")) for row in providers)
    missing_address = sum(not clean(row.get("address")) for row in providers)
    unique_npis = len({
        clean(row.get("npi")) for row in providers if clean(row.get("npi"))
    })
    specialties = Counter(
        clean(row.get("primary_specialty")) or "Unknown"
        for row in providers
    )

    return {
        "status": "passed" if not errors else "failed",
        "checked_at": utc_now(),
        "rows": len(providers),
        "source_rows": dict(sources),
        "unique_provider_ids": len(set(provider_ids)),
        "unique_npis": unique_npis,
        "duplicate_provider_ids": duplicate_ids,
        "missing_provider_names": missing_names,
        "wrong_state_rows": wrong_state,
        "invalid_npis": invalid_npis,
        "invalid_coordinate_rows": invalid_coordinates,
        "coordinate_rows": coordinate_rows,
        "missing_phone_rows": missing_phone,
        "missing_address_rows": missing_address,
        "unsupported_availability_claims": invalid_accepting,
        "top_specialties": dict(specialties.most_common(20)),
        "errors": errors,
    }
