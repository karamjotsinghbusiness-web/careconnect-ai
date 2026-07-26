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
Provider Data. The application loads the validated CSV files before using the
older workbook fallback.

The generated CSV may be used to find clinicians and health centers. It must
not be used to infer diagnosis, treatment, appointment availability, whether a
provider accepts new patients, or whether a provider is in a patient's
insurance network. Those facts require direct or transactional verification.

`provider_sources.json` records source URLs, release dates, row counts, and the
output checksum. `provider_quality_report.json` records validation results.
