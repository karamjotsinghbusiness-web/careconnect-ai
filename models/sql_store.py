"""Shared PostgreSQL access for validated public healthcare datasets."""

import logging
import os
from functools import lru_cache

import pandas as pd
from sqlalchemy import create_engine, text


logger = logging.getLogger(__name__)

DATASET_COUNT_QUERIES = {
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


def env_true(name, default="false"):
    return os.environ.get(name, default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def database_url():
    return os.environ.get("DATABASE_URL", "").strip()


def sqlalchemy_url(url):
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


@lru_cache(maxsize=2)
def get_database_engine(url):
    return create_engine(
        sqlalchemy_url(url),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
        pool_timeout=10,
    )


def dataset_is_current(connection, dataset_key):
    count_query = DATASET_COUNT_QUERIES.get(dataset_key)
    if count_query is None:
        return False
    try:
        expected_rows = connection.execute(
            text(
                """
                SELECT imported_rows
                FROM careconnect.dataset_imports
                WHERE dataset_key = :dataset_key
                  AND import_status = 'completed'
                  AND source_rows = imported_rows
                """
            ),
            {"dataset_key": dataset_key},
        ).scalar()
        if expected_rows is None:
            return False
        actual_rows = connection.execute(text(count_query)).scalar()
        return int(actual_rows) == int(expected_rows)
    except Exception:
        logger.info(
            "PostgreSQL dataset import metadata is not available for %s",
            dataset_key,
        )
        return False


def load_current_dataset(dataset_key, query):
    url = database_url()
    if not url:
        return None

    try:
        engine = get_database_engine(url)
        with engine.connect() as connection:
            if not dataset_is_current(connection, dataset_key):
                return None
            return pd.read_sql_query(text(query), connection)
    except Exception:
        if env_true("REQUIRE_DATABASE"):
            raise
        logger.exception(
            "PostgreSQL load failed for %s; using validated file fallback",
            dataset_key,
        )
        return None


def public_data_database_status():
    url = database_url()
    if not url:
        return {
            "database_configured": False,
            "storage_mode": "validated_files",
            "imports": [],
        }

    try:
        engine = get_database_engine(url)
        with engine.connect() as connection:
            import_rows = connection.execute(
                text(
                    """
                    SELECT
                        dataset_key,
                        target_table,
                        source_modified,
                        source_sha256,
                        source_rows,
                        imported_rows,
                        import_status,
                        imported_at
                    FROM careconnect.dataset_imports
                    ORDER BY dataset_key
                    """
                )
            ).mappings().all()
            counts = connection.execute(
                text(
                    """
                    SELECT
                        (SELECT count(*)
                         FROM careconnect.providers
                         WHERE is_active
                           AND (
                               source LIKE 'CMS Care Compare%'
                               OR source LIKE 'HRSA Health Center%'
                           )) AS providers,
                        (SELECT count(*) FROM careconnect.providers
                         WHERE is_active) AS all_active_providers,
                        (SELECT count(*) FROM careconnect.hospital_quality)
                            AS hospital_quality,
                        (SELECT count(*) FROM careconnect.hospice_quality)
                            AS hospice_quality
                    """
                )
            ).mappings().one()

        imports = []
        for row in import_rows:
            item = dict(row)
            item["source_sha256"] = str(item["source_sha256"])[:12]
            imported_at = item.get("imported_at")
            if imported_at is not None:
                item["imported_at"] = imported_at.isoformat()
            imports.append(item)
        return {
            "database_configured": True,
            "storage_mode": "postgresql",
            "counts": dict(counts),
            "imports": imports,
        }
    except Exception:
        logger.exception("Could not read public-data PostgreSQL status")
        return {
            "database_configured": True,
            "storage_mode": "database_unavailable_file_fallback",
            "imports": [],
        }
