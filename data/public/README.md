# CareConnect public provider data

This directory contains a validated, reproducible provider-discovery dataset.
It is intentionally separate from the synthetic `Patients` training sheet.

Run:

```bash
python scripts/sync_public_provider_data.py
```

The sync downloads current Missouri records from:

- CMS Doctors and Clinicians National Downloadable File
- HRSA Health Center Service Delivery and Look-Alike Sites

Hospital and hospice quality data are refreshed separately:

```bash
python scripts/sync_public_facility_data.py
```

That sync uses CMS Unplanned Hospital Visits - Hospital and CMS Hospice -
Provider Data.

## PostgreSQL storage

When `DATABASE_URL` is configured, application startup verifies the source
checksums and imports all three validated datasets into the normalized
`careconnect` schema. PostgreSQL is then the runtime source; the validated CSV
files remain a safe fallback if the database is not configured or available.

Run the same repeat-safe import manually with:

```bash
python scripts/import_public_data_postgres.py
```

The importer uses bulk `COPY`, validates row counts and unique keys, and
updates all three datasets transactionally. Re-running an unchanged import
does not duplicate rows. `GET /data/status` reports the storage mode,
non-sensitive row counts, source checksums, and import timestamps.

The generated CSV may be used to find clinicians and health centers. It must
not be used to infer diagnosis, treatment, appointment availability, whether a
provider accepts new patients, or whether a provider is in a patient's
insurance network. Those facts require direct or transactional verification.

`provider_sources.json` records source URLs, release dates, row counts, and the
output checksum. `provider_quality_report.json` records validation results.
