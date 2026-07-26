#!/usr/bin/env python3
"""Refresh CMS hospital and hospice quality data used by CareConnect."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parent.parent
PUBLIC_DIR = PROJECT_DIR / "data" / "public"
DEFAULT_MANIFEST = PUBLIC_DIR / "facility_sources.json"
DEFAULT_QUALITY = PUBLIC_DIR / "facility_quality_report.json"
USER_AGENT = "CareConnectAI-PublicFacilitySync/1.0"

DATASETS = {
    "hospital_quality": {
        "dataset_id": "632h-zaca",
        "title": "Unplanned Hospital Visits - Hospital",
        "landing_page": (
            "https://data.cms.gov/provider-data/dataset/632h-zaca"
        ),
        "output": PUBLIC_DIR / "hospital_quality_missouri.csv",
        "facility_id": "facility_id",
        "measure_id": "measure_id",
        "required": {
            "facility_id",
            "facility_name",
            "citytown",
            "state",
            "measure_id",
            "score",
            "start_date",
            "end_date",
        },
        "minimum_rows": 1_000,
        "minimum_facilities": 50,
    },
    "hospice_quality": {
        "dataset_id": "252m-zfp9",
        "title": "Hospice - Provider Data",
        "landing_page": (
            "https://data.cms.gov/provider-data/dataset/252m-zfp9"
        ),
        "output": PUBLIC_DIR / "hospice_quality_missouri.csv",
        "facility_id": "cms_certification_number_ccn",
        "measure_id": "measure_code",
        "required": {
            "cms_certification_number_ccn",
            "facility_name",
            "citytown",
            "state",
            "measure_code",
            "score",
            "measure_date_range",
        },
        "minimum_rows": 5_000,
        "minimum_facilities": 100,
    },
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean(value):
    return "" if value is None else str(value).strip()


def request_bytes(url, timeout=120, attempts=4):
    last_error = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,*/*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}") from last_error


def request_json(url, timeout=120):
    return json.loads(request_bytes(url, timeout=timeout).decode("utf-8"))


def metadata_url(dataset_id):
    return (
        "https://data.cms.gov/provider-data/api/1/metastore/"
        f"schemas/dataset/items/{dataset_id}"
    )


def query_url(dataset_id):
    return (
        "https://data.cms.gov/provider-data/api/1/datastore/"
        f"query/{dataset_id}/0"
    )


def fetch_missouri_rows(dataset_id, page_size=1500):
    rows = []
    offset = 0
    total = None
    while total is None or offset < total:
        query = urlencode(
            {
                "conditions[0][property]": "state",
                "conditions[0][value]": "MO",
                "conditions[0][operator]": "=",
                "limit": page_size,
                "offset": offset,
            }
        )
        payload = request_json(f"{query_url(dataset_id)}?{query}")
        page = payload.get("results", [])
        total = int(payload.get("count", len(page)))
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        print(
            f"{dataset_id}: downloaded {min(offset, total):,} "
            f"of {total:,} Missouri rows",
            flush=True,
        )
    return rows


def validate_dataset(rows, config):
    columns = set().union(*(row.keys() for row in rows)) if rows else set()
    missing_columns = sorted(config["required"] - columns)
    facility_id = config["facility_id"]
    measure_id = config["measure_id"]
    missing_facility_ids = sum(not clean(row.get(facility_id)) for row in rows)
    missing_names = sum(not clean(row.get("facility_name")) for row in rows)
    wrong_state = sum(clean(row.get("state")).upper() != "MO" for row in rows)
    facility_count = len({
        clean(row.get(facility_id))
        for row in rows
        if clean(row.get(facility_id))
    })
    measure_count = len({
        clean(row.get(measure_id))
        for row in rows
        if clean(row.get(measure_id))
    })
    keys = [
        (clean(row.get(facility_id)), clean(row.get(measure_id)))
        for row in rows
    ]
    duplicate_measure_rows = len(keys) - len(set(keys))
    missing_scores = sum(not clean(row.get("score")) for row in rows)

    errors = []
    if missing_columns:
        errors.append(f"missing required columns: {', '.join(missing_columns)}")
    if len(rows) < config["minimum_rows"]:
        errors.append(
            f"row count {len(rows)} is below {config['minimum_rows']}"
        )
    if facility_count < config["minimum_facilities"]:
        errors.append(
            f"facility count {facility_count} is below "
            f"{config['minimum_facilities']}"
        )
    if missing_facility_ids:
        errors.append(f"{missing_facility_ids} rows are missing facility IDs")
    if missing_names:
        errors.append(f"{missing_names} rows are missing facility names")
    if wrong_state:
        errors.append(f"{wrong_state} rows are outside Missouri")
    if duplicate_measure_rows:
        errors.append(
            f"{duplicate_measure_rows} duplicate facility/measure rows"
        )

    return {
        "status": "passed" if not errors else "failed",
        "rows": len(rows),
        "columns": len(columns),
        "facilities": facility_count,
        "measures": measure_count,
        "missing_facility_ids": missing_facility_ids,
        "missing_facility_names": missing_names,
        "wrong_state_rows": wrong_state,
        "duplicate_facility_measure_rows": duplicate_measure_rows,
        "missing_score_rows": missing_scores,
        "errors": errors,
    }


def atomic_write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    for row in rows[1:]:
        for column in row:
            if column not in fieldnames:
                fieldnames.append(column)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        writer = csv.DictWriter(
            temporary,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        json.dump(payload, temporary, indent=2, sort_keys=True)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync(manifest_path=DEFAULT_MANIFEST, quality_path=DEFAULT_QUALITY):
    generated_at = utc_now()
    downloaded = {}
    metadata = {}
    reports = {}

    for name, config in DATASETS.items():
        metadata[name] = request_json(metadata_url(config["dataset_id"]))
        downloaded[name] = fetch_missouri_rows(config["dataset_id"])
        reports[name] = validate_dataset(downloaded[name], config)

    quality = {
        "status": (
            "passed"
            if all(report["status"] == "passed" for report in reports.values())
            else "failed"
        ),
        "checked_at": utc_now(),
        "generated_at": generated_at,
        "datasets": reports,
    }
    atomic_write_json(quality_path, quality)
    if quality["status"] != "passed":
        errors = [
            f"{name}: {'; '.join(report['errors'])}"
            for name, report in reports.items()
            if report["errors"]
        ]
        raise RuntimeError("Facility data failed validation: " + " | ".join(errors))

    outputs = {}
    sources = []
    for name, config in DATASETS.items():
        rows = downloaded[name]
        atomic_write_csv(config["output"], rows)
        outputs[name] = {
            "path": str(config["output"].relative_to(PROJECT_DIR)),
            "rows": len(rows),
            "sha256": sha256_file(config["output"]),
        }
        source_metadata = metadata[name]
        sources.append(
            {
                "name": name,
                "agency": "Centers for Medicare & Medicaid Services (CMS)",
                "dataset": config["title"],
                "dataset_id": config["dataset_id"],
                "landing_page": config["landing_page"],
                "api": query_url(config["dataset_id"]),
                "modified": source_metadata.get("modified", ""),
                "released": source_metadata.get("released", ""),
                "rows_downloaded": len(rows),
                "intended_use": (
                    "Facility discovery and source-specific quality context; "
                    "not diagnosis, treatment selection, coverage, or "
                    "appointment availability."
                ),
            }
        )

    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "outputs": outputs,
        "quality_report": str(Path(quality_path).relative_to(PROJECT_DIR)),
        "sources": sources,
        "limitations": [
            (
                "CMS quality measures have source-defined measurement periods "
                "and are not real-time clinical outcomes."
            ),
            (
                "A facility listing does not prove insurance coverage, network "
                "status, appointment availability, or suitability for a patient."
            ),
        ],
    }
    atomic_write_json(manifest_path, manifest)
    return manifest, quality


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_QUALITY)
    return parser.parse_args()


def main():
    args = parse_args()
    manifest, quality = sync(args.manifest, args.quality_report)
    for name, output in manifest["outputs"].items():
        report = quality["datasets"][name]
        print(
            f"Published {name}: {output['rows']:,} rows, "
            f"{report['facilities']:,} facilities, "
            f"{report['measures']:,} measures"
        )


if __name__ == "__main__":
    main()
