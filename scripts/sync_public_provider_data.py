#!/usr/bin/env python3
"""Download, normalize, validate, and publish official Missouri provider data."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from models.public_provider_data import (  # noqa: E402
    CMS_DATASET_ID,
    CMS_DATASET_PAGE,
    CMS_METADATA_URL,
    CMS_QUERY_URL,
    HRSA_CSV_URL,
    HRSA_DATASET_PAGE,
    PROVIDER_COLUMNS,
    deduplicate_providers,
    normalize_cms_provider,
    normalize_hrsa_site,
    utc_now,
    validate_providers,
)


DEFAULT_OUTPUT = PROJECT_DIR / "data" / "public" / "providers_missouri.csv"
DEFAULT_MANIFEST = PROJECT_DIR / "data" / "public" / "provider_sources.json"
DEFAULT_QUALITY = PROJECT_DIR / "data" / "public" / "provider_quality_report.json"
USER_AGENT = "CareConnectAI-PublicDataSync/1.0"


def request_bytes(url, timeout=90, attempts=4):
    last_error = None
    for attempt in range(attempts):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json,text/csv,*/*",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Could not download {url}: {last_error}") from last_error


def request_json(url, timeout=90):
    return json.loads(request_bytes(url, timeout=timeout).decode("utf-8"))


def fetch_cms_rows(source_modified, page_size=1500, maximum_rows=None):
    rows = []
    offset = 0
    total = None
    while total is None or offset < total:
        limit = page_size
        if maximum_rows is not None:
            remaining = maximum_rows - len(rows)
            if remaining <= 0:
                break
            limit = min(limit, remaining)
        query = urlencode(
            {
                "conditions[0][property]": "state",
                "conditions[0][value]": "MO",
                "conditions[0][operator]": "=",
                "limit": limit,
                "offset": offset,
            }
        )
        payload = request_json(f"{CMS_QUERY_URL}?{query}", timeout=120)
        page = payload.get("results", [])
        total = int(payload.get("count", len(page)))
        if not page:
            break
        for row in page:
            provider = normalize_cms_provider(row, source_modified)
            if provider:
                rows.append(provider)
        offset += len(page)
        print(
            f"CMS: downloaded {min(offset, total):,} of {total:,} Missouri rows",
            flush=True,
        )
    return rows, total or 0


def fetch_hrsa_rows():
    raw = request_bytes(HRSA_CSV_URL, timeout=120)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    missouri_rows = []
    record_dates = []
    for row in reader:
        if (row.get("Site State Abbreviation") or "").strip().upper() != "MO":
            continue
        record_date = (row.get("Data Warehouse Record Create Date") or "").strip()
        if record_date:
            record_dates.append(record_date)
        missouri_rows.append((row, record_date))

    source_record_date = max(record_dates, default="")
    providers = []
    for row, record_date in missouri_rows:
        provider = normalize_hrsa_site(row, record_date or source_record_date)
        if provider:
            providers.append(provider)
    print(
        f"HRSA: downloaded {len(missouri_rows):,} Missouri rows; "
        f"accepted {len(providers):,} active sites",
        flush=True,
    )
    return providers, len(missouri_rows), source_record_date, len(raw)


def atomic_write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        writer = csv.DictWriter(temporary, fieldnames=PROVIDER_COLUMNS)
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


def sync(output, manifest_path, quality_path, cms_maximum_rows=None):
    generated_at = utc_now()
    cms_metadata = request_json(CMS_METADATA_URL)
    cms_modified = cms_metadata.get("modified", "")
    cms_released = cms_metadata.get("released", "")

    cms_providers, cms_downloaded = fetch_cms_rows(
        cms_modified,
        maximum_rows=cms_maximum_rows,
    )
    hrsa_providers, hrsa_downloaded, hrsa_record_date, hrsa_bytes = fetch_hrsa_rows()
    providers = deduplicate_providers(cms_providers + hrsa_providers)

    minimum_cms = 1 if cms_maximum_rows is not None else 25_000
    quality = validate_providers(
        providers,
        minimum_cms=minimum_cms,
        minimum_hrsa=100,
    )
    quality.update(
        {
            "generated_at": generated_at,
            "cms_rows_downloaded": cms_downloaded,
            "cms_rows_normalized_before_deduplication": len(cms_providers),
            "hrsa_rows_downloaded": hrsa_downloaded,
            "hrsa_rows_normalized_before_deduplication": len(hrsa_providers),
        }
    )
    atomic_write_json(quality_path, quality)
    if quality["status"] != "passed":
        raise RuntimeError(
            "Public provider data failed validation: "
            + "; ".join(quality["errors"])
        )

    atomic_write_csv(output, providers)
    checksum = sha256_file(output)
    manifest = {
        "schema_version": 1,
        "generated_at": generated_at,
        "intended_use": (
            "Provider and health-center discovery for Missouri care navigation."
        ),
        "prohibited_inferences": [
            "Do not use this file to diagnose or choose treatment.",
            "Do not infer that a provider is accepting new patients.",
            "Do not infer insurance network status or benefit coverage.",
            "Do not infer appointment availability.",
        ],
        "output": {
            "path": str(Path(output).relative_to(PROJECT_DIR)),
            "rows": len(providers),
            "sha256": checksum,
        },
        "sources": [
            {
                "agency": "Centers for Medicare & Medicaid Services (CMS)",
                "dataset": "Doctors and Clinicians National Downloadable File",
                "dataset_id": CMS_DATASET_ID,
                "landing_page": CMS_DATASET_PAGE,
                "api": CMS_QUERY_URL,
                "modified": cms_modified,
                "released": cms_released,
                "rows_downloaded": cms_downloaded,
                "usage": (
                    "Clinician identity, specialty, practice city/ZIP, "
                    "telephone, and group/facility affiliation."
                ),
            },
            {
                "agency": "Health Resources and Services Administration (HRSA)",
                "dataset": (
                    "Health Center Service Delivery and Look-Alike Sites"
                ),
                "landing_page": HRSA_DATASET_PAGE,
                "download": HRSA_CSV_URL,
                "record_date": hrsa_record_date,
                "download_bytes": hrsa_bytes,
                "rows_downloaded": hrsa_downloaded,
                "usage": (
                    "Active health-center site identity, location, phone, "
                    "organization, and HRSA-published coordinates."
                ),
                "usage_limitations": "None, according to the HRSA catalog.",
            },
        ],
        "quality_report": str(Path(quality_path).relative_to(PROJECT_DIR)),
    }
    atomic_write_json(manifest_path, manifest)
    return providers, manifest, quality


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--quality-report", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument(
        "--cms-maximum-rows",
        type=int,
        help="Development-only CMS row cap; omit for a production sync.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    providers, manifest, quality = sync(
        args.output,
        args.manifest,
        args.quality_report,
        cms_maximum_rows=args.cms_maximum_rows,
    )
    print(
        f"Published {len(providers):,} validated rows to {args.output} "
        f"(SHA-256 {manifest['output']['sha256'][:12]}…)"
    )
    print(
        f"Quality: {quality['status']}; "
        f"{quality['unique_npis']:,} unique NPIs; "
        f"{quality['coordinate_rows']:,} rows with coordinates"
    )


if __name__ == "__main__":
    main()
