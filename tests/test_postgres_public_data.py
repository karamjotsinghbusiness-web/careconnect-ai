import json
import os
import unittest
from pathlib import Path

import psycopg

from scripts.import_public_data_postgres import (
    import_public_data,
    psycopg_url,
)
from models.sql_store import sqlalchemy_url


PROJECT_DIR = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_POSTGRES_URL", "").strip()


class DatabaseUrlTests(unittest.TestCase):
    def test_railway_postgres_url_uses_installed_psycopg_driver(self):
        public_url = "postgresql://user:password@example.com:5432/careconnect"
        legacy_url = "postgres://user:password@example.com:5432/careconnect"

        self.assertEqual(
            sqlalchemy_url(public_url),
            "postgresql+psycopg://user:password@example.com:5432/careconnect",
        )
        self.assertEqual(
            sqlalchemy_url(legacy_url),
            "postgresql+psycopg://user:password@example.com:5432/careconnect",
        )
        self.assertEqual(psycopg_url(legacy_url), public_url)


@unittest.skipUnless(
    TEST_DATABASE_URL,
    "TEST_POSTGRES_URL is required for PostgreSQL integration tests",
)
class PostgresPublicDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        cls.first_import = import_public_data(TEST_DATABASE_URL, force=True)
        cls.provider_manifest = json.loads(
            (
                PROJECT_DIR / "data" / "public" / "provider_sources.json"
            ).read_text(encoding="utf-8")
        )
        cls.facility_manifest = json.loads(
            (
                PROJECT_DIR / "data" / "public" / "facility_sources.json"
            ).read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url

    def query_one(self, query):
        with psycopg.connect(psycopg_url(TEST_DATABASE_URL)) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                return cursor.fetchone()

    def test_all_manifest_rows_are_in_postgres(self):
        expected_provider_rows = self.provider_manifest["output"]["rows"]
        expected_hospital_rows = self.facility_manifest["outputs"][
            "hospital_quality"
        ]["rows"]
        expected_hospice_rows = self.facility_manifest["outputs"][
            "hospice_quality"
        ]["rows"]

        counts = self.query_one(
            """
            SELECT
                (SELECT count(*) FROM careconnect.providers
                 WHERE is_active = true),
                (SELECT count(*) FROM careconnect.hospital_quality),
                (SELECT count(*) FROM careconnect.hospice_quality),
                (SELECT count(*) FROM careconnect.dataset_imports
                 WHERE import_status = 'completed')
            """
        )

        self.assertEqual(
            counts,
            (
                expected_provider_rows,
                expected_hospital_rows,
                expected_hospice_rows,
                3,
            ),
        )

    def test_public_provider_claims_remain_unverified(self):
        unsupported_claims, wrong_state = self.query_one(
            """
            SELECT
                count(*) FILTER (
                    WHERE accepting_new_patients <> 'Unknown'
                ),
                count(*) FILTER (WHERE state <> 'MO')
            FROM careconnect.providers
            WHERE is_active = true
            """
        )

        self.assertEqual(unsupported_claims, 0)
        self.assertEqual(wrong_state, 0)

    def test_second_import_is_checksum_idempotent(self):
        result = import_public_data(TEST_DATABASE_URL)

        self.assertTrue(
            all(
                item["status"] == "current"
                for item in result["datasets"].values()
            )
        )

    def test_runtime_loaders_use_postgres_rows(self):
        from models.hospice import load_hospice
        from models.hospital_matcher import load_hospital_quality
        from models.provider_store import (
            _load_from_postgres,
            load_provider_data,
        )
        from models.sql_store import get_database_engine

        get_database_engine.cache_clear()
        _load_from_postgres.cache_clear()

        providers = load_provider_data(
            PROJECT_DIR
            / "data"
            / "missouri_healthcare_linked_dataset_with_expanded_symptoms.xlsx"
        )
        hospitals = load_hospital_quality()
        hospices = load_hospice()

        self.assertEqual(
            len(providers),
            self.provider_manifest["output"]["rows"],
        )
        self.assertEqual(
            len(hospitals),
            self.facility_manifest["outputs"]["hospital_quality"]["rows"],
        )
        self.assertEqual(
            len(hospices),
            self.facility_manifest["outputs"]["hospice_quality"]["rows"],
        )


if __name__ == "__main__":
    unittest.main()
