"""Provider data access with PostgreSQL-first and Excel fallback behavior."""

import logging
import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from models.public_provider_data import PROVIDER_COLUMNS
from models.sql_store import (
    dataset_is_current,
    env_true,
    get_database_engine,
)


logger = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PUBLIC_PROVIDER_DATA = (
    PROJECT_DIR / "data" / "public" / "providers_missouri.csv"
)

PROVIDER_QUERY = """
SELECT
    provider_id,
    npi,
    provider_name,
    gender,
    credential,
    primary_specialty,
    secondary_specialty,
    specialty,
    organization,
    city,
    state,
    zip_code,
    phone,
    accepting_new_patients,
    source,
    address,
    latitude,
    longitude
FROM careconnect.providers
WHERE is_active = true
ORDER BY provider_id
"""


@lru_cache(maxsize=2)
def _load_public_csv(csv_path, modified_time):
    del modified_time
    providers = pd.read_csv(
        csv_path,
        dtype={
            "provider_id": "string",
            "npi": "string",
            "zip_code": "string",
            "phone": "string",
        },
    )
    missing = sorted(set(PROVIDER_COLUMNS) - set(providers.columns))
    if missing:
        raise ValueError(f"Public provider file is missing columns: {missing}")
    return providers[PROVIDER_COLUMNS]


def _with_public_provider_data(providers):
    if not env_true("ENABLE_PUBLIC_PROVIDER_DATA", "true"):
        return providers

    csv_path = Path(
        os.environ.get(
            "PUBLIC_PROVIDER_DATA_PATH",
            str(DEFAULT_PUBLIC_PROVIDER_DATA),
        )
    )
    if not csv_path.exists():
        if env_true("REQUIRE_PUBLIC_PROVIDER_DATA"):
            raise FileNotFoundError(
                f"Required public provider file not found: {csv_path}"
            )
        logger.warning("Public provider file not found: %s", csv_path)
        return providers

    public = _load_public_csv(
        str(csv_path),
        csv_path.stat().st_mtime_ns,
    ).copy()
    providers = providers.rename(columns={"Address": "address"}).copy()
    providers.columns = providers.columns.str.lower().str.strip()
    combined = pd.concat([providers, public], ignore_index=True, sort=False)
    if "provider_id" in combined.columns:
        combined = combined.drop_duplicates(
            subset=["provider_id"],
            keep="last",
        )
    logger.info(
        "Merged %s validated public provider rows; %s total rows",
        len(public),
        len(combined),
    )
    return combined


@lru_cache(maxsize=1)
def _load_from_postgres(database_url):
    from sqlalchemy import text

    engine = get_database_engine(database_url)
    with engine.connect() as connection:
        providers = pd.read_sql_query(text(PROVIDER_QUERY), connection)
        includes_public_data = dataset_is_current(
            connection,
            "providers_missouri_public",
        )
    logger.info("Loaded %s active providers from PostgreSQL", len(providers))
    return providers, includes_public_data


def load_provider_data(excel_path):
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        try:
            providers, includes_public_data = _load_from_postgres(
                database_url
            )
            providers = providers.copy()
            if includes_public_data:
                return providers
            return _with_public_provider_data(providers)
        except Exception:
            if env_true("REQUIRE_DATABASE"):
                raise
            logger.exception("PostgreSQL provider load failed; using the Excel fallback")

    providers = pd.read_excel(Path(excel_path), sheet_name="Providers")
    providers.columns = providers.columns.str.lower().str.strip()
    logger.info("Loaded %s providers from the Excel fallback", len(providers))
    return _with_public_provider_data(providers)
