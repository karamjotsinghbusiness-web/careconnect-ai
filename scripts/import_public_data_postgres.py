#!/usr/bin/env python3
"""Import validated CareConnect public datasets into PostgreSQL."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql


PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

PUBLIC_DIR = PROJECT_DIR / "data" / "public"
MIGRATION_PATH = (
    PROJECT_DIR
    / "database"
    / "migrations"
    / "004_public_health_data.sql"
)
LOGGER = logging.getLogger("careconnect.public_data_import")

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

HOSPITAL_COLUMNS = [
    "facility_id",
    "facility_name",
    "address",
    "citytown",
    "state",
    "zip_code",
    "countyparish",
    "telephone_number",
    "measure_id",
    "measure_name",
    "compared_to_national",
    "denominator",
    "score",
    "lower_estimate",
    "higher_estimate",
    "number_of_patients",
    "number_of_patients_returned",
    "footnote",
    "start_date",
    "end_date",
]

HOSPICE_COLUMNS = [
    "cms_certification_number_ccn",
    "facility_name",
    "address_line_1",
    "address_line_2",
    "citytown",
    "state",
    "zip_code",
    "countyparish",
    "telephone_number",
    "cms_region",
    "measure_code",
    "measure_name",
    "score",
    "footnote",
    "measure_date_range",
]


def env_true(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def psycopg_url(url):
    if url.startswith("postgresql+psycopg://"):
        return "postgresql://" + url.removeprefix("postgresql+psycopg://")
    if url.startswith("postgres://"):
        return "postgresql://" + url.removeprefix("postgres://")
    return url


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_sources():
    provider_manifest = read_json(PUBLIC_DIR / "provider_sources.json")
    provider_quality = read_json(PUBLIC_DIR / "provider_quality_report.json")
    facility_manifest = read_json(PUBLIC_DIR / "facility_sources.json")
    facility_quality = read_json(PUBLIC_DIR / "facility_quality_report.json")

    if provider_quality.get("status") != "passed":
        raise ValueError("Provider quality report has not passed")
    if facility_quality.get("status") != "passed":
        raise ValueError("Facility quality report has not passed")

    provider_path = PUBLIC_DIR / "providers_missouri.csv"
    hospital_path = PUBLIC_DIR / "hospital_quality_missouri.csv"
    hospice_path = PUBLIC_DIR / "hospice_quality_missouri.csv"
    paths = {
        "providers_missouri_public": provider_path,
        "hospital_quality_missouri": hospital_path,
        "hospice_quality_missouri": hospice_path,
    }
    expected = {
        "providers_missouri_public": provider_manifest["output"],
        "hospital_quality_missouri": facility_manifest["outputs"][
            "hospital_quality"
        ],
        "hospice_quality_missouri": facility_manifest["outputs"][
            "hospice_quality"
        ],
    }

    for dataset_key, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"Validated source file is missing: {path}")
        checksum = sha256_file(path)
        if checksum != expected[dataset_key]["sha256"]:
            raise ValueError(
                f"Checksum mismatch for {dataset_key}: "
                "refusing database import"
            )

    return {
        "providers_missouri_public": {
            "path": provider_path,
            "rows": int(provider_manifest["output"]["rows"]),
            "sha256": provider_manifest["output"]["sha256"],
            "target_table": "careconnect.providers",
            "source_url": "CMS Care Compare + HRSA Health Center Program",
            "source_modified": provider_manifest["generated_at"],
            "manifest": provider_manifest,
        },
        "hospital_quality_missouri": {
            "path": hospital_path,
            "rows": int(
                facility_manifest["outputs"]["hospital_quality"]["rows"]
            ),
            "sha256": facility_manifest["outputs"]["hospital_quality"][
                "sha256"
            ],
            "target_table": "careconnect.hospital_quality",
            "source_url": next(
                source["landing_page"]
                for source in facility_manifest["sources"]
                if source["name"] == "hospital_quality"
            ),
            "source_modified": next(
                source["modified"]
                for source in facility_manifest["sources"]
                if source["name"] == "hospital_quality"
            ),
            "manifest": facility_manifest,
        },
        "hospice_quality_missouri": {
            "path": hospice_path,
            "rows": int(
                facility_manifest["outputs"]["hospice_quality"]["rows"]
            ),
            "sha256": facility_manifest["outputs"]["hospice_quality"][
                "sha256"
            ],
            "target_table": "careconnect.hospice_quality",
            "source_url": next(
                source["landing_page"]
                for source in facility_manifest["sources"]
                if source["name"] == "hospice_quality"
            ),
            "source_modified": next(
                source["modified"]
                for source in facility_manifest["sources"]
                if source["name"] == "hospice_quality"
            ),
            "manifest": facility_manifest,
        },
    }


def apply_migration(connection):
    migration = MIGRATION_PATH.read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(migration, prepare=False)
    connection.commit()


def copy_csv(cursor, table_name, columns, path):
    copy_statement = sql.SQL(
        "COPY {} ({}) FROM STDIN "
        "WITH (FORMAT CSV, HEADER TRUE, NULL '', ENCODING 'UTF8')"
    ).format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
    )
    with cursor.copy(copy_statement) as copy:
        with Path(path).open("r", encoding="utf-8", newline="") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), ""):
                copy.write(chunk)


def import_is_current(cursor, dataset_key, descriptor):
    count_queries = {
        "providers_missouri_public": """
            SELECT count(*)
            FROM careconnect.providers
            WHERE is_active
              AND (
                  source LIKE 'CMS Care Compare%'
                  OR source LIKE 'HRSA Health Center%'
              )
        """,
        "hospital_quality_missouri": """
            SELECT count(*) FROM careconnect.hospital_quality
        """,
        "hospice_quality_missouri": """
            SELECT count(*) FROM careconnect.hospice_quality
        """,
    }
    cursor.execute(
        """
        SELECT
            source_sha256 = %s
            AND imported_rows = %s
            AND import_status = 'completed'
        FROM careconnect.dataset_imports
        WHERE dataset_key = %s
        """,
        (descriptor["sha256"], descriptor["rows"], dataset_key),
    )
    row = cursor.fetchone()
    cursor.execute(count_queries[dataset_key])
    table_rows = cursor.fetchone()[0]
    return bool(row and row[0] and table_rows == descriptor["rows"])


def record_import(cursor, dataset_key, descriptor, imported_rows):
    cursor.execute(
        """
        INSERT INTO careconnect.dataset_imports (
            dataset_key,
            target_table,
            source_file,
            source_url,
            source_modified,
            source_sha256,
            source_rows,
            imported_rows,
            import_status,
            source_manifest,
            imported_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            'completed', %s::jsonb, now()
        )
        ON CONFLICT (dataset_key) DO UPDATE SET
            target_table = EXCLUDED.target_table,
            source_file = EXCLUDED.source_file,
            source_url = EXCLUDED.source_url,
            source_modified = EXCLUDED.source_modified,
            source_sha256 = EXCLUDED.source_sha256,
            source_rows = EXCLUDED.source_rows,
            imported_rows = EXCLUDED.imported_rows,
            import_status = EXCLUDED.import_status,
            source_manifest = EXCLUDED.source_manifest,
            imported_at = now()
        """,
        (
            dataset_key,
            descriptor["target_table"],
            descriptor["path"].name,
            descriptor["source_url"],
            descriptor["source_modified"],
            descriptor["sha256"],
            descriptor["rows"],
            imported_rows,
            json.dumps(descriptor["manifest"], sort_keys=True),
        ),
    )


def import_providers(cursor, descriptor):
    cursor.execute(
        """
        CREATE TEMP TABLE provider_import_stage (
            provider_id text,
            npi text,
            provider_name text,
            gender text,
            credential text,
            primary_specialty text,
            secondary_specialty text,
            specialty text,
            organization text,
            city text,
            state text,
            zip_code text,
            phone text,
            accepting_new_patients text,
            source text,
            address text,
            latitude text,
            longitude text
        ) ON COMMIT DROP
        """
    )
    copy_csv(
        cursor,
        "provider_import_stage",
        PROVIDER_COLUMNS,
        descriptor["path"],
    )
    cursor.execute(
        """
        SELECT
            count(*),
            count(DISTINCT provider_id),
            count(*) FILTER (
                WHERE provider_id IS NULL
                   OR provider_name IS NULL
                   OR state <> 'MO'
            )
        FROM provider_import_stage
        """
    )
    source_rows, unique_ids, invalid_rows = cursor.fetchone()
    if source_rows != descriptor["rows"]:
        raise ValueError(
            f"Provider SQL staging count {source_rows} does not match "
            f"manifest {descriptor['rows']}"
        )
    if unique_ids != source_rows or invalid_rows:
        raise ValueError("Provider SQL staging validation failed")

    cursor.execute(
        """
        INSERT INTO careconnect.providers (
            provider_id, npi, provider_name, gender, credential,
            primary_specialty, secondary_specialty, specialty,
            organization, city, state, zip_code, phone,
            accepting_new_patients, source, address, latitude,
            longitude, is_active, updated_at
        )
        SELECT
            trim(provider_id),
            NULLIF(trim(npi), ''),
            trim(provider_name),
            NULLIF(trim(gender), ''),
            NULLIF(trim(credential), ''),
            NULLIF(trim(primary_specialty), ''),
            NULLIF(trim(secondary_specialty), ''),
            NULLIF(trim(specialty), ''),
            NULLIF(trim(organization), ''),
            NULLIF(trim(city), ''),
            upper(trim(state)),
            NULLIF(trim(zip_code), ''),
            NULLIF(trim(phone), ''),
            'Unknown',
            trim(source),
            NULLIF(trim(address), ''),
            NULLIF(trim(latitude), '')::double precision,
            NULLIF(trim(longitude), '')::double precision,
            true,
            now()
        FROM provider_import_stage
        ON CONFLICT (provider_id) DO UPDATE SET
            npi = EXCLUDED.npi,
            provider_name = EXCLUDED.provider_name,
            gender = EXCLUDED.gender,
            credential = EXCLUDED.credential,
            primary_specialty = EXCLUDED.primary_specialty,
            secondary_specialty = EXCLUDED.secondary_specialty,
            specialty = EXCLUDED.specialty,
            organization = EXCLUDED.organization,
            city = EXCLUDED.city,
            state = EXCLUDED.state,
            zip_code = EXCLUDED.zip_code,
            phone = EXCLUDED.phone,
            accepting_new_patients = 'Unknown',
            source = EXCLUDED.source,
            address = EXCLUDED.address,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            is_active = true,
            updated_at = now()
        """
    )
    cursor.execute(
        """
        UPDATE careconnect.providers AS provider
        SET is_active = false, updated_at = now()
        WHERE (
                provider.source LIKE 'CMS Care Compare%'
                OR provider.source LIKE 'HRSA Health Center%'
            )
          AND NOT EXISTS (
              SELECT 1
              FROM provider_import_stage AS stage
              WHERE stage.provider_id = provider.provider_id
          )
        """
    )
    return source_rows


def import_hospital_quality(cursor, descriptor):
    cursor.execute(
        """
        CREATE TEMP TABLE hospital_import_stage (
            facility_id text,
            facility_name text,
            address text,
            citytown text,
            state text,
            zip_code text,
            countyparish text,
            telephone_number text,
            measure_id text,
            measure_name text,
            compared_to_national text,
            denominator text,
            score text,
            lower_estimate text,
            higher_estimate text,
            number_of_patients text,
            number_of_patients_returned text,
            footnote text,
            start_date text,
            end_date text
        ) ON COMMIT DROP
        """
    )
    copy_csv(
        cursor,
        "hospital_import_stage",
        HOSPITAL_COLUMNS,
        descriptor["path"],
    )
    cursor.execute(
        """
        SELECT
            count(*),
            count(DISTINCT (facility_id, measure_id)),
            count(*) FILTER (
                WHERE facility_id IS NULL
                   OR facility_name IS NULL
                   OR measure_id IS NULL
                   OR state <> 'MO'
            )
        FROM hospital_import_stage
        """
    )
    source_rows, unique_keys, invalid_rows = cursor.fetchone()
    if source_rows != descriptor["rows"]:
        raise ValueError("Hospital SQL staging count does not match manifest")
    if unique_keys != source_rows or invalid_rows:
        raise ValueError("Hospital SQL staging validation failed")

    cursor.execute(
        """
        INSERT INTO careconnect.hospital_quality (
            facility_id, facility_name, address, city_town, state,
            zip_code, county_parish, telephone_number, measure_id,
            measure_name, compared_to_national, denominator, score,
            lower_estimate, higher_estimate, number_of_patients,
            number_of_patients_returned, footnote, start_date,
            end_date, updated_at
        )
        SELECT
            trim(facility_id),
            trim(facility_name),
            NULLIF(trim(address), ''),
            NULLIF(trim(citytown), ''),
            upper(trim(state)),
            NULLIF(trim(zip_code), ''),
            NULLIF(trim(countyparish), ''),
            NULLIF(trim(telephone_number), ''),
            trim(measure_id),
            NULLIF(trim(measure_name), ''),
            NULLIF(trim(compared_to_national), ''),
            NULLIF(trim(denominator), ''),
            NULLIF(trim(score), ''),
            NULLIF(trim(lower_estimate), ''),
            NULLIF(trim(higher_estimate), ''),
            NULLIF(trim(number_of_patients), ''),
            NULLIF(trim(number_of_patients_returned), ''),
            NULLIF(trim(footnote), ''),
            NULLIF(trim(start_date), ''),
            NULLIF(trim(end_date), ''),
            now()
        FROM hospital_import_stage
        ON CONFLICT (facility_id, measure_id) DO UPDATE SET
            facility_name = EXCLUDED.facility_name,
            address = EXCLUDED.address,
            city_town = EXCLUDED.city_town,
            state = EXCLUDED.state,
            zip_code = EXCLUDED.zip_code,
            county_parish = EXCLUDED.county_parish,
            telephone_number = EXCLUDED.telephone_number,
            measure_name = EXCLUDED.measure_name,
            compared_to_national = EXCLUDED.compared_to_national,
            denominator = EXCLUDED.denominator,
            score = EXCLUDED.score,
            lower_estimate = EXCLUDED.lower_estimate,
            higher_estimate = EXCLUDED.higher_estimate,
            number_of_patients = EXCLUDED.number_of_patients,
            number_of_patients_returned =
                EXCLUDED.number_of_patients_returned,
            footnote = EXCLUDED.footnote,
            start_date = EXCLUDED.start_date,
            end_date = EXCLUDED.end_date,
            updated_at = now()
        """
    )
    cursor.execute(
        """
        DELETE FROM careconnect.hospital_quality AS target
        WHERE NOT EXISTS (
            SELECT 1
            FROM hospital_import_stage AS stage
            WHERE stage.facility_id = target.facility_id
              AND stage.measure_id = target.measure_id
        )
        """
    )
    return source_rows


def import_hospice_quality(cursor, descriptor):
    cursor.execute(
        """
        CREATE TEMP TABLE hospice_import_stage (
            cms_certification_number_ccn text,
            facility_name text,
            address_line_1 text,
            address_line_2 text,
            citytown text,
            state text,
            zip_code text,
            countyparish text,
            telephone_number text,
            cms_region text,
            measure_code text,
            measure_name text,
            score text,
            footnote text,
            measure_date_range text
        ) ON COMMIT DROP
        """
    )
    copy_csv(
        cursor,
        "hospice_import_stage",
        HOSPICE_COLUMNS,
        descriptor["path"],
    )
    cursor.execute(
        """
        SELECT
            count(*),
            count(DISTINCT (
                cms_certification_number_ccn,
                measure_code
            )),
            count(*) FILTER (
                WHERE cms_certification_number_ccn IS NULL
                   OR facility_name IS NULL
                   OR measure_code IS NULL
                   OR state <> 'MO'
            )
        FROM hospice_import_stage
        """
    )
    source_rows, unique_keys, invalid_rows = cursor.fetchone()
    if source_rows != descriptor["rows"]:
        raise ValueError("Hospice SQL staging count does not match manifest")
    if unique_keys != source_rows or invalid_rows:
        raise ValueError("Hospice SQL staging validation failed")

    cursor.execute(
        """
        INSERT INTO careconnect.hospice_quality (
            facility_id, facility_name, address, address_line_2,
            city_town, state, zip_code, county_parish,
            telephone_number, cms_region, measure_id, measure_name,
            score, footnote, measure_date_range, updated_at
        )
        SELECT
            trim(cms_certification_number_ccn),
            trim(facility_name),
            NULLIF(trim(address_line_1), ''),
            NULLIF(trim(address_line_2), ''),
            NULLIF(trim(citytown), ''),
            upper(trim(state)),
            NULLIF(trim(zip_code), ''),
            NULLIF(trim(countyparish), ''),
            NULLIF(trim(telephone_number), ''),
            NULLIF(trim(cms_region), ''),
            trim(measure_code),
            NULLIF(trim(measure_name), ''),
            NULLIF(trim(score), ''),
            NULLIF(trim(footnote), ''),
            NULLIF(trim(measure_date_range), ''),
            now()
        FROM hospice_import_stage
        ON CONFLICT (facility_id, measure_id) DO UPDATE SET
            facility_name = EXCLUDED.facility_name,
            address = EXCLUDED.address,
            address_line_2 = EXCLUDED.address_line_2,
            city_town = EXCLUDED.city_town,
            state = EXCLUDED.state,
            zip_code = EXCLUDED.zip_code,
            county_parish = EXCLUDED.county_parish,
            telephone_number = EXCLUDED.telephone_number,
            cms_region = EXCLUDED.cms_region,
            measure_name = EXCLUDED.measure_name,
            score = EXCLUDED.score,
            footnote = EXCLUDED.footnote,
            measure_date_range = EXCLUDED.measure_date_range,
            updated_at = now()
        """
    )
    cursor.execute(
        """
        DELETE FROM careconnect.hospice_quality AS target
        WHERE NOT EXISTS (
            SELECT 1
            FROM hospice_import_stage AS stage
            WHERE stage.cms_certification_number_ccn =
                    target.facility_id
              AND stage.measure_code = target.measure_id
        )
        """
    )
    return source_rows


IMPORTERS = {
    "providers_missouri_public": import_providers,
    "hospital_quality_missouri": import_hospital_quality,
    "hospice_quality_missouri": import_hospice_quality,
}


def import_public_data(database_url, force=False):
    descriptors = verify_sources()
    result = {"status": "completed", "datasets": {}}
    with psycopg.connect(
        psycopg_url(database_url),
        connect_timeout=10,
        application_name="careconnect-public-data-import",
    ) as connection:
        apply_migration(connection)
        for dataset_key, descriptor in descriptors.items():
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL lock_timeout = '10s'")
                    cursor.execute("SET LOCAL statement_timeout = '120s'")
                    cursor.execute(
                        """
                        SELECT pg_advisory_xact_lock(
                            hashtext('careconnect_public_data_import')
                        )
                        """
                    )
                    if (
                        not force
                        and import_is_current(
                            cursor,
                            dataset_key,
                            descriptor,
                        )
                    ):
                        result["datasets"][dataset_key] = {
                            "status": "current",
                            "rows": descriptor["rows"],
                        }
                        continue
                    imported_rows = IMPORTERS[dataset_key](
                        cursor,
                        descriptor,
                    )
                    record_import(
                        cursor,
                        dataset_key,
                        descriptor,
                        imported_rows,
                    )
                    result["datasets"][dataset_key] = {
                        "status": "imported",
                        "rows": imported_rows,
                    }

        with connection.cursor() as cursor:
            cursor.execute("ANALYZE careconnect.providers")
            cursor.execute("ANALYZE careconnect.hospital_quality")
            cursor.execute("ANALYZE careconnect.hospice_quality")
        connection.commit()
    return result


def initialize_public_data_database():
    database_url = (
        os.environ.get("DATABASE_MIGRATION_URL", "").strip()
        or os.environ.get("DATABASE_URL", "").strip()
    )
    if not database_url:
        return {
            "status": "not_configured",
            "storage_mode": "validated_files",
        }
    if not env_true("AUTO_IMPORT_PUBLIC_DATA", "true"):
        return {
            "status": "disabled",
            "storage_mode": "database_without_auto_import",
        }

    try:
        return import_public_data(database_url)
    except Exception:
        LOGGER.exception("PostgreSQL public-data initialization failed")
        if env_true("REQUIRE_PUBLIC_DATA_DATABASE"):
            raise
        return {
            "status": "failed",
            "storage_mode": "validated_file_fallback",
        }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=(
            os.environ.get("DATABASE_MIGRATION_URL", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        ),
    )
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main():
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    if not args.database_url:
        raise SystemExit(
            "Set DATABASE_MIGRATION_URL or DATABASE_URL before importing"
        )
    print(json.dumps(import_public_data(args.database_url, args.force), indent=2))


if __name__ == "__main__":
    main()
