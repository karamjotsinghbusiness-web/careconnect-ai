"""Export only the approved Providers sheet for PostgreSQL bulk import."""

import csv
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WORKBOOK = PROJECT_DIR / "data" / "missouri_healthcare_linked_dataset_with_expanded_symptoms.xlsx"
DEFAULT_OUTPUT = PROJECT_DIR / "database" / "imports" / "providers.csv"
DEFAULT_PUBLIC_DATA = PROJECT_DIR / "data" / "public" / "providers_missouri.csv"

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


def export_providers(
    workbook_path=DEFAULT_WORKBOOK,
    output_path=DEFAULT_OUTPUT,
    public_data_path=DEFAULT_PUBLIC_DATA,
):
    workbook_path = Path(workbook_path)
    output_path = Path(output_path)
    public_data_path = Path(public_data_path)

    providers = pd.read_excel(
        workbook_path,
        sheet_name="Providers",
        dtype={
            "provider_id": "string",
            "npi": "string",
            "zip_code": "string",
            "phone": "string",
        },
    )
    providers = providers.rename(columns={"Address": "address"})

    if public_data_path.exists():
        public_providers = pd.read_csv(
            public_data_path,
            dtype={
                "provider_id": "string",
                "npi": "string",
                "zip_code": "string",
                "phone": "string",
            },
        )
        providers = pd.concat(
            [providers, public_providers],
            ignore_index=True,
            sort=False,
        )

    missing_columns = sorted(set(PROVIDER_COLUMNS) - set(providers.columns))
    if missing_columns:
        raise ValueError(f"Providers sheet is missing columns: {missing_columns}")

    providers = providers[PROVIDER_COLUMNS].copy()
    providers["provider_id"] = providers["provider_id"].str.strip()
    providers["provider_name"] = providers["provider_name"].astype("string").str.strip()
    providers["state"] = providers["state"].astype("string").str.strip().str.upper()
    providers = providers.drop_duplicates(subset=["provider_id"], keep="last")

    if providers["provider_id"].isna().any() or providers["provider_id"].duplicated().any():
        raise ValueError("provider_id must be complete and unique before import")
    if providers["provider_name"].isna().any():
        raise ValueError("provider_name cannot be empty")
    if not providers["state"].dropna().str.fullmatch(r"[A-Z]{2}").all():
        raise ValueError("state values must use two-letter codes")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    providers.to_csv(
        output_path,
        index=False,
        encoding="utf-8",
        na_rep="",
        quoting=csv.QUOTE_MINIMAL,
    )
    return len(providers), output_path


if __name__ == "__main__":
    row_count, exported_path = export_providers()
    print(f"Exported {row_count} provider rows to {exported_path}")
