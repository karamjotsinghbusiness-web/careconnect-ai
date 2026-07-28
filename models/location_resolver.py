"""Resolve Missouri cities and counties to authoritative Census coordinates."""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
GEOGRAPHY_DIR = BASE_DIR / "data" / "geography"
COUNTY_GAZETTEER_PATH = GEOGRAPHY_DIR / "2025_gaz_counties_29.txt"
PLACE_GAZETTEER_PATH = GEOGRAPHY_DIR / "2025_gaz_place_29.txt"
PLACE_SUFFIXES = (" city", " town", " village", " cdp")


@dataclass(frozen=True)
class LocationResolution:
    name: str
    geoid: str
    latitude: float
    longitude: float
    kind: str

    @property
    def coordinates(self):
        return self.latitude, self.longitude


def normalize_location(value):
    normalized = str(value or "").casefold().strip()
    normalized = re.sub(r"[.,]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized = re.sub(r"(?:\s+missouri|\s+mo)$", "", normalized).strip()
    normalized = re.sub(r"^saint\s+", "st ", normalized)
    normalized = normalized.replace("saint louis", "st louis")
    normalized = normalized.replace("ste genevieve", "st genevieve")
    return normalized


def is_explicit_county(value):
    normalized = normalize_location(value)
    return normalized.endswith(" county")


def _place_name(name):
    normalized = normalize_location(name)
    for suffix in PLACE_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)].strip()
    return normalized


def _read_gazetteer(path):
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        for row in reader:
            if row.get("USPS") != "MO":
                continue
            yield row


@lru_cache(maxsize=1)
def missouri_places():
    places = {}
    for row in _read_gazetteer(PLACE_GAZETTEER_PATH):
        key = _place_name(row["NAME"])
        places[key] = LocationResolution(
            name=row["NAME"],
            geoid=row["GEOID"],
            latitude=float(row["INTPTLAT"]),
            longitude=float(row["INTPTLONG"]),
            kind="place",
        )
    return places


@lru_cache(maxsize=1)
def missouri_counties():
    counties = {}
    for row in _read_gazetteer(COUNTY_GAZETTEER_PATH):
        full_name = normalize_location(row["NAME"])
        resolution = LocationResolution(
            name=row["NAME"],
            geoid=row["GEOID"],
            latitude=float(row["INTPTLAT"]),
            longitude=float(row["INTPTLONG"]),
            kind="county",
        )
        counties[full_name] = resolution
        if full_name.endswith(" county"):
            counties[full_name.removesuffix(" county").strip()] = resolution
    return counties


def resolve_missouri_location(value, allow_bare_county=False):
    """Resolve an exact Census place or county name without unsafe fuzzy guesses."""
    normalized = normalize_location(value)
    if not normalized:
        return None

    place = missouri_places().get(normalized)
    if place is not None:
        return place

    if is_explicit_county(normalized) or allow_bare_county:
        return missouri_counties().get(normalized)

    if normalized == "st louis city":
        return missouri_counties().get(normalized)

    return None


def _distance_miles(latitude, longitude, location):
    """Return straight-line miles from coordinates to a Census location."""
    radius_miles = 3958.8
    lat1, lon1, lat2, lon2 = map(
        math.radians,
        [
            float(latitude),
            float(longitude),
            location.latitude,
            location.longitude,
        ],
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return radius_miles * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def nearest_missouri_place(latitude, longitude, max_distance_miles=75):
    """Resolve coordinates to the nearest Census place within the service area."""
    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None

    nearest = None
    nearest_distance = float("inf")
    for location in missouri_places().values():
        distance = _distance_miles(latitude, longitude, location)
        if distance < nearest_distance:
            nearest = location
            nearest_distance = distance

    if nearest is None or nearest_distance > max_distance_miles:
        return None
    return nearest
