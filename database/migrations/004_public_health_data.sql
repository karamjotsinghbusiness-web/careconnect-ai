BEGIN;

CREATE SCHEMA IF NOT EXISTS careconnect;
REVOKE ALL ON SCHEMA careconnect FROM PUBLIC;

CREATE TABLE IF NOT EXISTS careconnect.providers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_id text NOT NULL UNIQUE,
    npi text,
    provider_name text NOT NULL,
    gender text,
    credential text,
    primary_specialty text,
    secondary_specialty text,
    specialty text,
    organization text,
    city text,
    state text NOT NULL,
    zip_code text,
    phone text,
    accepting_new_patients text NOT NULL DEFAULT 'Unknown',
    source text NOT NULL,
    address text,
    latitude double precision,
    longitude double precision,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT providers_state_format_check
        CHECK (state ~ '^[A-Z]{2}$'),
    CONSTRAINT providers_accepting_check
        CHECK (accepting_new_patients IN ('Yes', 'No', 'Maybe', 'Unknown')),
    CONSTRAINT providers_latitude_check
        CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CONSTRAINT providers_longitude_check
        CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);

CREATE TABLE IF NOT EXISTS careconnect.hospital_quality (
    facility_id text NOT NULL,
    facility_name text NOT NULL,
    address text,
    city_town text,
    state text NOT NULL,
    zip_code text,
    county_parish text,
    telephone_number text,
    measure_id text NOT NULL,
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
    end_date text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, measure_id),
    CONSTRAINT hospital_quality_state_format_check
        CHECK (state ~ '^[A-Z]{2}$')
);

CREATE TABLE IF NOT EXISTS careconnect.hospice_quality (
    facility_id text NOT NULL,
    facility_name text NOT NULL,
    address text,
    address_line_2 text,
    city_town text,
    state text NOT NULL,
    zip_code text,
    county_parish text,
    telephone_number text,
    cms_region text,
    measure_id text NOT NULL,
    measure_name text,
    score text,
    footnote text,
    measure_date_range text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (facility_id, measure_id),
    CONSTRAINT hospice_quality_state_format_check
        CHECK (state ~ '^[A-Z]{2}$')
);

CREATE TABLE IF NOT EXISTS careconnect.dataset_imports (
    dataset_key text PRIMARY KEY,
    target_table text NOT NULL,
    source_file text NOT NULL,
    source_url text NOT NULL,
    source_modified text,
    source_sha256 text NOT NULL,
    source_rows bigint NOT NULL CHECK (source_rows >= 0),
    imported_rows bigint NOT NULL CHECK (imported_rows >= 0),
    import_status text NOT NULL,
    source_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    imported_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT dataset_imports_status_check
        CHECK (import_status IN ('completed', 'failed')),
    CONSTRAINT dataset_imports_sha256_check
        CHECK (source_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS providers_active_state_city_specialty_idx
    ON careconnect.providers (state, city, primary_specialty)
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS providers_active_npi_idx
    ON careconnect.providers (npi)
    WHERE is_active = true AND npi IS NOT NULL;

CREATE INDEX IF NOT EXISTS hospital_quality_state_city_idx
    ON careconnect.hospital_quality (state, city_town);

CREATE INDEX IF NOT EXISTS hospital_quality_measure_state_idx
    ON careconnect.hospital_quality (measure_id, state);

CREATE INDEX IF NOT EXISTS hospice_quality_state_city_idx
    ON careconnect.hospice_quality (state, city_town);

CREATE INDEX IF NOT EXISTS hospice_quality_measure_state_idx
    ON careconnect.hospice_quality (measure_id, state);

COMMIT;
