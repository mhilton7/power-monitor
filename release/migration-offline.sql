BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260720_0001

CREATE TABLE roles (
    name VARCHAR(32) NOT NULL,
    description VARCHAR(255) NOT NULL,
    CONSTRAINT pk_roles PRIMARY KEY (name)
);

CREATE TABLE users (
    id VARCHAR(36) NOT NULL,
    email VARCHAR(320) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL,
    is_active BOOLEAN NOT NULL,
    password_changed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_users PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE audit_events (
    id VARCHAR(36) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    actor_type VARCHAR(24) NOT NULL,
    actor_id VARCHAR(36),
    action VARCHAR(120) NOT NULL,
    object_type VARCHAR(80),
    object_id VARCHAR(80),
    source_ip VARCHAR(64),
    outcome VARCHAR(24) NOT NULL,
    details JSON NOT NULL,
    CONSTRAINT pk_audit_events PRIMARY KEY (id)
);

CREATE INDEX ix_audit_events_action ON audit_events (action);

CREATE INDEX ix_audit_events_occurred_at ON audit_events (occurred_at);

CREATE INDEX ix_audit_events_actor_id ON audit_events (actor_id);

CREATE TABLE sites (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    allowed_cidrs JSON NOT NULL,
    allowed_domains JSON NOT NULL,
    allow_public_polling BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_sites PRIMARY KEY (id),
    CONSTRAINT uq_sites_name UNIQUE (name)
);

CREATE TABLE utilities (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    website VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utilities PRIMARY KEY (id),
    CONSTRAINT uq_utilities_name UNIQUE (name)
);

CREATE TABLE notification_channels (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(120) NOT NULL,
    channel_type VARCHAR(24) NOT NULL,
    encrypted_config BYTEA NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_notification_channels PRIMARY KEY (id)
);

CREATE TABLE firmware_releases (
    id VARCHAR(36) NOT NULL,
    version VARCHAR(80) NOT NULL,
    channel VARCHAR(24) NOT NULL,
    hardware_target VARCHAR(120) NOT NULL,
    protocol_min VARCHAR(40) NOT NULL,
    protocol_max VARCHAR(40) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    signature TEXT NOT NULL,
    signing_key_id VARCHAR(128) NOT NULL,
    release_notes TEXT NOT NULL,
    verified_at TIMESTAMP WITH TIME ZONE NOT NULL,
    active BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_firmware_releases PRIMARY KEY (id),
    CONSTRAINT uq_firmware_releases_sha256 UNIQUE (sha256)
);

CREATE TABLE backup_runs (
    id VARCHAR(36) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(24) NOT NULL,
    path VARCHAR(500),
    manifest_hash VARCHAR(64),
    verified_at TIMESTAMP WITH TIME ZONE,
    verification_details JSON NOT NULL,
    CONSTRAINT pk_backup_runs PRIMARY KEY (id)
);

CREATE INDEX ix_backup_runs_started_at ON backup_runs (started_at);

CREATE TABLE worker_state (
    worker_name VARCHAR(80) NOT NULL,
    instance_id VARCHAR(80) NOT NULL,
    last_loop_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_success_at TIMESTAMP WITH TIME ZONE,
    status VARCHAR(24) NOT NULL,
    details JSON NOT NULL,
    CONSTRAINT pk_worker_state PRIMARY KEY (worker_name)
);

CREATE TABLE user_roles (
    user_id VARCHAR(36) NOT NULL,
    role_name VARCHAR(32) NOT NULL,
    CONSTRAINT pk_user_roles PRIMARY KEY (user_id, role_name),
    CONSTRAINT fk_user_roles_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_roles_role_name_roles FOREIGN KEY(role_name) REFERENCES roles (name) ON DELETE RESTRICT
);

CREATE TABLE sessions (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    csrf_hash VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    revoked_at TIMESTAMP WITH TIME ZONE,
    source_ip VARCHAR(64),
    user_agent VARCHAR(512),
    CONSTRAINT pk_sessions PRIMARY KEY (id),
    CONSTRAINT fk_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT uq_sessions_token_hash UNIQUE (token_hash)
);

CREATE INDEX ix_sessions_expires_at ON sessions (expires_at);

CREATE INDEX ix_sessions_user_id ON sessions (user_id);

CREATE TABLE totp_credentials (
    user_id VARCHAR(36) NOT NULL,
    encrypted_secret BYTEA NOT NULL,
    confirmed BOOLEAN NOT NULL,
    last_counter INTEGER,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_totp_credentials PRIMARY KEY (user_id),
    CONSTRAINT fk_totp_credentials_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE TABLE utility_accounts (
    id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    utility_id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    billing_cycle_start_day INTEGER NOT NULL,
    baseline_allocation_kwh NUMERIC(14, 6),
    generation_provider VARCHAR(32) NOT NULL,
    active_rate_version_id VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utility_accounts PRIMARY KEY (id),
    CONSTRAINT ck_utility_accounts_billing_cycle_start_day CHECK (billing_cycle_start_day >= 1 AND billing_cycle_start_day <= 31),
    CONSTRAINT fk_utility_accounts_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_accounts_utility_id_utilities FOREIGN KEY(utility_id) REFERENCES utilities (id) ON DELETE RESTRICT
);

CREATE INDEX ix_utility_accounts_site_id ON utility_accounts (site_id);

CREATE INDEX ix_utility_accounts_utility_id ON utility_accounts (utility_id);

CREATE TABLE circuits (
    id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    parent_id VARCHAR(36),
    name VARCHAR(160) NOT NULL,
    measurement_role VARCHAR(32) NOT NULL,
    split_phase_group VARCHAR(80),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_circuits PRIMARY KEY (id),
    CONSTRAINT uq_circuit_site_name UNIQUE (site_id, name),
    CONSTRAINT ck_circuits_measurement_role CHECK (measurement_role IN ('main','service-leg','branch','submeter','informational')),
    CONSTRAINT fk_circuits_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_circuits_parent_id_circuits FOREIGN KEY(parent_id) REFERENCES circuits (id) ON DELETE RESTRICT
);

CREATE INDEX ix_circuits_site_id ON circuits (site_id);

CREATE TABLE enrollment_tokens (
    id VARCHAR(36) NOT NULL,
    token_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    consumed_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    preassignment JSON NOT NULL,
    CONSTRAINT pk_enrollment_tokens PRIMARY KEY (id),
    CONSTRAINT uq_enrollment_tokens_token_hash UNIQUE (token_hash),
    CONSTRAINT fk_enrollment_tokens_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_enrollment_tokens_expires_at ON enrollment_tokens (expires_at);

CREATE TABLE rate_plans (
    id VARCHAR(36) NOT NULL,
    utility_id VARCHAR(36) NOT NULL,
    code VARCHAR(80) NOT NULL,
    name VARCHAR(160) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_plans PRIMARY KEY (id),
    CONSTRAINT uq_rate_plan_utility_code UNIQUE (utility_id, code),
    CONSTRAINT fk_rate_plans_utility_id_utilities FOREIGN KEY(utility_id) REFERENCES utilities (id) ON DELETE RESTRICT
);

CREATE INDEX ix_rate_plans_utility_id ON rate_plans (utility_id);

CREATE TABLE export_jobs (
    id VARCHAR(36) NOT NULL,
    requested_by VARCHAR(36) NOT NULL,
    format VARCHAR(8) NOT NULL,
    query JSON NOT NULL,
    status VARCHAR(24) NOT NULL,
    file_path VARCHAR(500),
    content_hash VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_export_jobs PRIMARY KEY (id),
    CONSTRAINT fk_export_jobs_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_export_jobs_requested_by ON export_jobs (requested_by);

CREATE TABLE report_definitions (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    report_type VARCHAR(40) NOT NULL,
    configuration JSON NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_report_definitions PRIMARY KEY (id),
    CONSTRAINT fk_report_definitions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE TABLE aggregate_sets (
    id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36),
    name VARCHAR(160) NOT NULL,
    cost_scope VARCHAR(32) NOT NULL,
    is_default BOOLEAN NOT NULL,
    overlap_confirmed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_aggregate_sets PRIMARY KEY (id),
    CONSTRAINT ck_aggregate_sets_cost_scope CHECK (cost_scope IN ('energy_only','allocated_account','full_account')),
    CONSTRAINT fk_aggregate_sets_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_aggregate_sets_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE SET NULL
);

CREATE INDEX ix_aggregate_sets_site_id ON aggregate_sets (site_id);

CREATE TABLE devices (
    id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36),
    circuit_id VARCHAR(36),
    hardware_id VARCHAR(128) NOT NULL,
    name VARCHAR(160) NOT NULL,
    connection_mode VARCHAR(16) NOT NULL,
    measurement_role VARCHAR(32) NOT NULL,
    cost_scope VARCHAR(32) NOT NULL,
    include_in_default_site_total BOOLEAN NOT NULL,
    ct_rating_amps NUMERIC(8, 3) NOT NULL,
    protocol_version VARCHAR(40) NOT NULL,
    firmware_version VARCHAR(80),
    firmware_build_hash VARCHAR(128),
    status VARCHAR(48) NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    maintenance_until TIMESTAMP WITH TIME ZONE,
    desired_config_version INTEGER NOT NULL,
    effective_config_version INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_devices PRIMARY KEY (id),
    CONSTRAINT ck_devices_connection_mode CHECK (connection_mode IN ('pull','push','hybrid')),
    CONSTRAINT ck_devices_cost_scope CHECK (cost_scope IN ('energy_only','allocated_account','full_account')),
    CONSTRAINT ck_devices_ct_rating CHECK (ct_rating_amps > 0),
    CONSTRAINT fk_devices_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_devices_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE SET NULL,
    CONSTRAINT fk_devices_circuit_id_circuits FOREIGN KEY(circuit_id) REFERENCES circuits (id) ON DELETE SET NULL,
    CONSTRAINT uq_devices_hardware_id UNIQUE (hardware_id)
);

CREATE INDEX ix_devices_site_id ON devices (site_id);

CREATE INDEX ix_devices_last_seen_at ON devices (last_seen_at);

CREATE INDEX ix_devices_status ON devices (status);

CREATE TABLE rate_versions (
    id VARCHAR(36) NOT NULL,
    rate_plan_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    timezone VARCHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    source_url VARCHAR(500) NOT NULL,
    source_checked_on DATE NOT NULL,
    source_notes TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    immutable_after_use BOOLEAN NOT NULL,
    is_active BOOLEAN NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_by VARCHAR(36),
    CONSTRAINT pk_rate_versions PRIMARY KEY (id),
    CONSTRAINT uq_rate_version_number UNIQUE (rate_plan_id, version),
    CONSTRAINT fk_rate_versions_rate_plan_id_rate_plans FOREIGN KEY(rate_plan_id) REFERENCES rate_plans (id) ON DELETE RESTRICT,
    CONSTRAINT fk_rate_versions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_rate_versions_rate_plan_id ON rate_versions (rate_plan_id);

CREATE TABLE billing_cycles (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL,
    ends_at TIMESTAMP WITH TIME ZONE NOT NULL,
    explicit_meter_dates BOOLEAN NOT NULL,
    finalized_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_billing_cycles PRIMARY KEY (id),
    CONSTRAINT fk_billing_cycles_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT
);

CREATE INDEX ix_billing_cycles_utility_account_id ON billing_cycles (utility_account_id);

CREATE TABLE generated_reports (
    id VARCHAR(36) NOT NULL,
    definition_id VARCHAR(36),
    requested_by VARCHAR(36) NOT NULL,
    status VARCHAR(24) NOT NULL,
    file_path VARCHAR(500),
    data_coverage JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_generated_reports PRIMARY KEY (id),
    CONSTRAINT fk_generated_reports_definition_id_report_definitions FOREIGN KEY(definition_id) REFERENCES report_definitions (id) ON DELETE SET NULL,
    CONSTRAINT fk_generated_reports_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE TABLE aggregate_members (
    id VARCHAR(36) NOT NULL,
    aggregate_set_id VARCHAR(36) NOT NULL,
    circuit_id VARCHAR(36),
    device_id VARCHAR(36),
    allocation_percent NUMERIC(7, 4) NOT NULL,
    CONSTRAINT pk_aggregate_members PRIMARY KEY (id),
    CONSTRAINT ck_aggregate_members_single_member_target CHECK ((circuit_id IS NOT NULL) <> (device_id IS NOT NULL)),
    CONSTRAINT ck_aggregate_members_allocation_percent CHECK (allocation_percent > 0 AND allocation_percent <= 100),
    CONSTRAINT fk_aggregate_members_aggregate_set_id_aggregate_sets FOREIGN KEY(aggregate_set_id) REFERENCES aggregate_sets (id) ON DELETE CASCADE,
    CONSTRAINT fk_aggregate_members_circuit_id_circuits FOREIGN KEY(circuit_id) REFERENCES circuits (id) ON DELETE RESTRICT
);

CREATE INDEX ix_aggregate_members_aggregate_set_id ON aggregate_members (aggregate_set_id);

CREATE TABLE device_credentials (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    encrypted_secret BYTEA NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    valid_from TIMESTAMP WITH TIME ZONE NOT NULL,
    valid_until TIMESTAMP WITH TIME ZONE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    delivered_at TIMESTAMP WITH TIME ZONE,
    confirmed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_device_credentials PRIMARY KEY (id),
    CONSTRAINT fk_device_credentials_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_credentials_fingerprint ON device_credentials (fingerprint);

CREATE INDEX ix_device_credentials_device_id ON device_credentials (device_id);

CREATE TABLE device_addresses (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    scheme VARCHAR(8) NOT NULL,
    source VARCHAR(24) NOT NULL,
    is_manual_override BOOLEAN NOT NULL,
    first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL,
    validation_error VARCHAR(500),
    CONSTRAINT pk_device_addresses PRIMARY KEY (id),
    CONSTRAINT fk_device_addresses_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_addresses_device_id ON device_addresses (device_id);

CREATE TABLE device_capabilities (
    device_id VARCHAR(36) NOT NULL,
    hardware_target VARCHAR(120) NOT NULL,
    pzem_model VARCHAR(120) NOT NULL,
    sd_required BOOLEAN NOT NULL,
    features JSON NOT NULL,
    reported_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_device_capabilities PRIMARY KEY (device_id),
    CONSTRAINT fk_device_capabilities_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE TABLE device_config_versions (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    version INTEGER NOT NULL,
    desired_config JSON NOT NULL,
    config_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    report JSON,
    created_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reported_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_device_config_versions PRIMARY KEY (id),
    CONSTRAINT uq_device_config_version UNIQUE (device_id, version),
    CONSTRAINT fk_device_config_versions_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE,
    CONSTRAINT fk_device_config_versions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_device_config_versions_device_id ON device_config_versions (device_id);

CREATE TABLE device_status_snapshots (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(48) NOT NULL,
    evidence JSON NOT NULL,
    CONSTRAINT pk_device_status_snapshots PRIMARY KEY (id),
    CONSTRAINT fk_device_status_snapshots_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_status_snapshots_captured_at ON device_status_snapshots (captured_at);

CREATE INDEX ix_device_status_snapshots_device_id ON device_status_snapshots (device_id);

CREATE TABLE device_heartbeats (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    boot_id VARCHAR(36) NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    device_time TIMESTAMP WITH TIME ZONE,
    source_ip VARCHAR(64),
    current_watts NUMERIC(14, 4),
    rssi_dbm INTEGER,
    pzem_ok BOOLEAN NOT NULL,
    sd_ok BOOLEAN NOT NULL,
    time_trusted BOOLEAN NOT NULL,
    newest_sequence INTEGER NOT NULL,
    backlog_estimate INTEGER NOT NULL,
    payload JSON NOT NULL,
    CONSTRAINT pk_device_heartbeats PRIMARY KEY (id),
    CONSTRAINT fk_device_heartbeats_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_heartbeats_device_id ON device_heartbeats (device_id);

CREATE INDEX ix_device_heartbeats_received_at ON device_heartbeats (received_at);

CREATE TABLE device_events (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    event_id VARCHAR(80) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    received_at TIMESTAMP WITH TIME ZONE NOT NULL,
    category VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    evidence JSON NOT NULL,
    CONSTRAINT pk_device_events PRIMARY KEY (id),
    CONSTRAINT uq_device_event UNIQUE (device_id, event_id),
    CONSTRAINT fk_device_events_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_events_category ON device_events (category);

CREATE INDEX ix_device_events_occurred_at ON device_events (occurred_at);

CREATE INDEX ix_device_events_device_id ON device_events (device_id);

CREATE TABLE sync_cursors (
    device_id VARCHAR(36) NOT NULL,
    highest_contiguous_sequence INTEGER NOT NULL,
    maximum_seen_sequence INTEGER NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_sync_cursors PRIMARY KEY (device_id),
    CONSTRAINT fk_sync_cursors_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE TABLE sequence_gaps (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    start_sequence INTEGER NOT NULL,
    end_sequence INTEGER NOT NULL,
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL,
    resolved_at TIMESTAMP WITH TIME ZONE,
    permanent_loss BOOLEAN NOT NULL,
    CONSTRAINT pk_sequence_gaps PRIMARY KEY (id),
    CONSTRAINT ck_sequence_gaps_gap_bounds CHECK (start_sequence > 0 AND end_sequence >= start_sequence),
    CONSTRAINT uq_sequence_gap UNIQUE (device_id, start_sequence, end_sequence),
    CONSTRAINT fk_sequence_gaps_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_sequence_gaps_device_id ON sequence_gaps (device_id);

CREATE TABLE device_nonces (
    device_id VARCHAR(36) NOT NULL,
    direction VARCHAR(24) NOT NULL,
    nonce_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_device_nonces PRIMARY KEY (device_id, direction, nonce_hash),
    CONSTRAINT fk_device_nonces_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_device_nonces_expires_at ON device_nonces (expires_at);

CREATE TABLE raw_readings (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    sequence INTEGER NOT NULL,
    boot_id VARCHAR(36) NOT NULL,
    interval_start TIMESTAMP WITH TIME ZONE NOT NULL,
    interval_end TIMESTAMP WITH TIME ZONE NOT NULL,
    time_trusted BOOLEAN NOT NULL,
    voltage_avg NUMERIC(12, 4),
    voltage_min NUMERIC(12, 4),
    voltage_max NUMERIC(12, 4),
    current_avg NUMERIC(12, 5),
    current_min NUMERIC(12, 5),
    current_max NUMERIC(12, 5),
    power_avg NUMERIC(16, 5),
    power_min NUMERIC(16, 5),
    power_max NUMERIC(16, 5),
    power_factor NUMERIC(7, 5),
    frequency_hz NUMERIC(8, 4),
    pzem_energy_start_wh NUMERIC(20, 4),
    pzem_energy_end_wh NUMERIC(20, 4),
    device_lifetime_energy_wh NUMERIC(24, 4),
    device_interval_energy_wh NUMERIC(18, 6),
    energy_method VARCHAR(40) NOT NULL,
    ct_rating_amps NUMERIC(8, 3) NOT NULL,
    quality_flags JSON NOT NULL,
    firmware_version VARCHAR(80) NOT NULL,
    record_hash VARCHAR(64) NOT NULL,
    original_payload JSON,
    ingestion_source VARCHAR(8) NOT NULL,
    ingested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_raw_readings PRIMARY KEY (id),
    CONSTRAINT uq_raw_device_sequence UNIQUE (device_id, sequence),
    CONSTRAINT ck_raw_readings_positive_sequence CHECK (sequence > 0),
    CONSTRAINT ck_raw_readings_valid_interval CHECK (interval_end > interval_start),
    CONSTRAINT ck_raw_readings_ingestion_source CHECK (ingestion_source IN ('pull','push')),
    CONSTRAINT ck_raw_readings_power_factor CHECK (power_factor IS NULL OR (power_factor >= 0 AND power_factor <= 1)),
    CONSTRAINT fk_raw_readings_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT,
    CONSTRAINT fk_raw_readings_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT
);

CREATE INDEX ix_raw_device_time ON raw_readings (device_id, interval_start);

CREATE INDEX ix_raw_site_time ON raw_readings (site_id, interval_start);

CREATE INDEX ix_raw_readings_ingested_at ON raw_readings (ingested_at);

CREATE TABLE daily_device_rollups (
    device_id VARCHAR(36) NOT NULL,
    local_date DATE NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    energy_wh NUMERIC(24, 6) NOT NULL,
    peak_watts NUMERIC(18, 5) NOT NULL,
    coverage_percent NUMERIC(7, 4) NOT NULL,
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_daily_device_rollups PRIMARY KEY (device_id, local_date),
    CONSTRAINT fk_daily_device_rollups_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT
);

CREATE TABLE monthly_device_rollups (
    device_id VARCHAR(36) NOT NULL,
    month_start DATE NOT NULL,
    energy_wh NUMERIC(24, 6) NOT NULL,
    peak_watts NUMERIC(18, 5) NOT NULL,
    coverage_percent NUMERIC(7, 4) NOT NULL,
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_monthly_device_rollups PRIMARY KEY (device_id, month_start),
    CONSTRAINT fk_monthly_device_rollups_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT
);

CREATE TABLE site_rollups (
    aggregate_set_id VARCHAR(36) NOT NULL,
    interval_start TIMESTAMP WITH TIME ZONE NOT NULL,
    resolution VARCHAR(16) NOT NULL,
    energy_wh NUMERIC(24, 6) NOT NULL,
    peak_watts NUMERIC(18, 5) NOT NULL,
    coverage_percent NUMERIC(7, 4) NOT NULL,
    CONSTRAINT pk_site_rollups PRIMARY KEY (aggregate_set_id, interval_start, resolution),
    CONSTRAINT fk_site_rollups_aggregate_set_id_aggregate_sets FOREIGN KEY(aggregate_set_id) REFERENCES aggregate_sets (id) ON DELETE RESTRICT
);

CREATE TABLE rate_seasons (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    name VARCHAR(32) NOT NULL,
    start_month INTEGER NOT NULL,
    start_day INTEGER NOT NULL,
    end_month INTEGER NOT NULL,
    end_day INTEGER NOT NULL,
    CONSTRAINT pk_rate_seasons PRIMARY KEY (id),
    CONSTRAINT fk_rate_seasons_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_seasons_rate_version_id ON rate_seasons (rate_version_id);

CREATE TABLE rate_day_types (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    name VARCHAR(32) NOT NULL,
    weekdays JSON NOT NULL,
    holiday_behavior VARCHAR(32) NOT NULL,
    holiday_source VARCHAR(500),
    CONSTRAINT pk_rate_day_types PRIMARY KEY (id),
    CONSTRAINT fk_rate_day_types_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_day_types_rate_version_id ON rate_day_types (rate_version_id);

CREATE TABLE rate_periods (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    season_name VARCHAR(32) NOT NULL,
    day_type VARCHAR(32) NOT NULL,
    start_minute INTEGER NOT NULL,
    end_minute INTEGER NOT NULL,
    bucket VARCHAR(40) NOT NULL,
    price_per_kwh NUMERIC(14, 8) NOT NULL,
    CONSTRAINT pk_rate_periods PRIMARY KEY (id),
    CONSTRAINT ck_rate_periods_period_start CHECK (start_minute >= 0 AND start_minute < 1440),
    CONSTRAINT ck_rate_periods_period_end CHECK (end_minute > 0 AND end_minute <= 1440),
    CONSTRAINT ck_rate_periods_period_order CHECK (end_minute > start_minute),
    CONSTRAINT fk_rate_periods_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_periods_rate_version_id ON rate_periods (rate_version_id);

CREATE TABLE baseline_rules (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    credit_per_kwh NUMERIC(14, 8) NOT NULL,
    requires_full_account BOOLEAN NOT NULL,
    allocation_source VARCHAR(80) NOT NULL,
    CONSTRAINT pk_baseline_rules PRIMARY KEY (id),
    CONSTRAINT fk_baseline_rules_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_baseline_rules_rate_version_id ON baseline_rules (rate_version_id);

CREATE TABLE fixed_charge_rules (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    name VARCHAR(120) NOT NULL,
    amount_per_day NUMERIC(14, 8) NOT NULL,
    account_once BOOLEAN NOT NULL,
    CONSTRAINT pk_fixed_charge_rules PRIMARY KEY (id),
    CONSTRAINT fk_fixed_charge_rules_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_fixed_charge_rules_rate_version_id ON fixed_charge_rules (rate_version_id);

CREATE TABLE rate_adjustments (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    name VARCHAR(120) NOT NULL,
    component VARCHAR(40) NOT NULL,
    operation VARCHAR(24) NOT NULL,
    amount NUMERIC(16, 8) NOT NULL,
    configuration JSON NOT NULL,
    CONSTRAINT pk_rate_adjustments PRIMARY KEY (id),
    CONSTRAINT fk_rate_adjustments_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_adjustments_rate_version_id ON rate_adjustments (rate_version_id);

CREATE TABLE cost_calculation_runs (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    aggregate_set_id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    input_start TIMESTAMP WITH TIME ZONE NOT NULL,
    input_end TIMESTAMP WITH TIME ZONE NOT NULL,
    algorithm_version VARCHAR(40) NOT NULL,
    status VARCHAR(24) NOT NULL,
    coverage_percent NUMERIC(7, 4) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_cost_calculation_runs PRIMARY KEY (id),
    CONSTRAINT fk_cost_calculation_runs_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_cost_calculation_runs_aggregate_set_id_aggregate_sets FOREIGN KEY(aggregate_set_id) REFERENCES aggregate_sets (id) ON DELETE RESTRICT,
    CONSTRAINT fk_cost_calculation_runs_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE RESTRICT
);

CREATE INDEX ix_cost_calculation_runs_utility_account_id ON cost_calculation_runs (utility_account_id);

CREATE TABLE manual_bill_adjustments (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    billing_cycle_id VARCHAR(36),
    name VARCHAR(160) NOT NULL,
    amount NUMERIC(16, 4) NOT NULL,
    notes TEXT NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_manual_bill_adjustments PRIMARY KEY (id),
    CONSTRAINT fk_manual_bill_adjustments_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_manual_bill_adjustments_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_manual_bill_adjustments_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_manual_bill_adjustments_utility_account_id ON manual_bill_adjustments (utility_account_id);

CREATE TABLE alert_rules (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    rule_type VARCHAR(80) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    enabled BOOLEAN NOT NULL,
    site_id VARCHAR(36),
    device_id VARCHAR(36),
    debounce_seconds INTEGER NOT NULL,
    resolve_seconds INTEGER NOT NULL,
    configuration JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_alert_rules PRIMARY KEY (id),
    CONSTRAINT fk_alert_rules_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE CASCADE,
    CONSTRAINT fk_alert_rules_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE CASCADE
);

CREATE INDEX ix_alert_rules_rule_type ON alert_rules (rule_type);

CREATE TABLE firmware_deployments (
    id VARCHAR(36) NOT NULL,
    firmware_release_id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    status VARCHAR(32) NOT NULL,
    scheduled_at TIMESTAMP WITH TIME ZONE NOT NULL,
    downloaded_at TIMESTAMP WITH TIME ZONE,
    installed_at TIMESTAMP WITH TIME ZONE,
    validated_at TIMESTAMP WITH TIME ZONE,
    failure_reason VARCHAR(500),
    rollback_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(36) NOT NULL,
    CONSTRAINT pk_firmware_deployments PRIMARY KEY (id),
    CONSTRAINT fk_firmware_deployments_firmware_release_id_firmware_releases FOREIGN KEY(firmware_release_id) REFERENCES firmware_releases (id) ON DELETE RESTRICT,
    CONSTRAINT fk_firmware_deployments_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT,
    CONSTRAINT fk_firmware_deployments_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_firmware_deployments_firmware_release_id ON firmware_deployments (firmware_release_id);

CREATE INDEX ix_firmware_deployments_device_id ON firmware_deployments (device_id);

CREATE TABLE normalized_intervals (
    id VARCHAR(36) NOT NULL,
    raw_reading_id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    interval_start TIMESTAMP WITH TIME ZONE NOT NULL,
    interval_end TIMESTAMP WITH TIME ZONE NOT NULL,
    device_energy_wh NUMERIC(18, 6),
    server_energy_wh NUMERIC(18, 6),
    selected_energy_wh NUMERIC(18, 6),
    selected_method VARCHAR(40) NOT NULL,
    validation_result VARCHAR(32) NOT NULL,
    validation_reason VARCHAR(500) NOT NULL,
    algorithm_version VARCHAR(32) NOT NULL,
    CONSTRAINT pk_normalized_intervals PRIMARY KEY (id),
    CONSTRAINT uq_normalized_intervals_raw_reading_id UNIQUE (raw_reading_id),
    CONSTRAINT fk_normalized_intervals_raw_reading_id_raw_readings FOREIGN KEY(raw_reading_id) REFERENCES raw_readings (id) ON DELETE RESTRICT,
    CONSTRAINT fk_normalized_intervals_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT
);

CREATE INDEX ix_normalized_intervals_interval_start ON normalized_intervals (interval_start);

CREATE INDEX ix_normalized_intervals_device_id ON normalized_intervals (device_id);

CREATE TABLE daily_cost_rollups (
    run_id VARCHAR(36) NOT NULL,
    local_date DATE NOT NULL,
    bucket VARCHAR(40) NOT NULL,
    component VARCHAR(40) NOT NULL,
    energy_kwh NUMERIC(20, 9) NOT NULL,
    unrounded_cost NUMERIC(24, 12) NOT NULL,
    CONSTRAINT pk_daily_cost_rollups PRIMARY KEY (run_id, local_date, bucket, component),
    CONSTRAINT fk_daily_cost_rollups_run_id_cost_calculation_runs FOREIGN KEY(run_id) REFERENCES cost_calculation_runs (id) ON DELETE CASCADE
);

CREATE TABLE alert_instances (
    id VARCHAR(36) NOT NULL,
    rule_id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36),
    site_id VARCHAR(36),
    status VARCHAR(24) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    opened_at TIMESTAMP WITH TIME ZONE NOT NULL,
    acknowledged_at TIMESTAMP WITH TIME ZONE,
    acknowledged_by VARCHAR(36),
    resolved_at TIMESTAMP WITH TIME ZONE,
    silenced_until TIMESTAMP WITH TIME ZONE,
    evidence JSON NOT NULL,
    CONSTRAINT pk_alert_instances PRIMARY KEY (id),
    CONSTRAINT fk_alert_instances_rule_id_alert_rules FOREIGN KEY(rule_id) REFERENCES alert_rules (id) ON DELETE RESTRICT,
    CONSTRAINT fk_alert_instances_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE SET NULL,
    CONSTRAINT fk_alert_instances_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE SET NULL,
    CONSTRAINT fk_alert_instances_acknowledged_by_users FOREIGN KEY(acknowledged_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_alert_instances_status ON alert_instances (status);

CREATE INDEX ix_alert_instances_rule_id ON alert_instances (rule_id);

CREATE INDEX ix_alert_instances_device_id ON alert_instances (device_id);

CREATE TABLE cost_interval_results (
    id VARCHAR(36) NOT NULL,
    run_id VARCHAR(36) NOT NULL,
    normalized_interval_id VARCHAR(36),
    interval_start TIMESTAMP WITH TIME ZONE NOT NULL,
    interval_end TIMESTAMP WITH TIME ZONE NOT NULL,
    bucket VARCHAR(40) NOT NULL,
    energy_kwh NUMERIC(20, 9) NOT NULL,
    price_per_kwh NUMERIC(14, 8) NOT NULL,
    unrounded_cost NUMERIC(24, 12) NOT NULL,
    component VARCHAR(40) NOT NULL,
    CONSTRAINT pk_cost_interval_results PRIMARY KEY (id),
    CONSTRAINT fk_cost_interval_results_run_id_cost_calculation_runs FOREIGN KEY(run_id) REFERENCES cost_calculation_runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_cost_interval_results_normalized_interval_id_normali_ec91 FOREIGN KEY(normalized_interval_id) REFERENCES normalized_intervals (id) ON DELETE RESTRICT
);

CREATE INDEX ix_cost_interval_results_run_id ON cost_interval_results (run_id);

CREATE INDEX ix_cost_interval_results_interval_start ON cost_interval_results (interval_start);

CREATE TABLE notification_attempts (
    id VARCHAR(36) NOT NULL,
    alert_instance_id VARCHAR(36),
    channel_id VARCHAR(36) NOT NULL,
    attempted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    status VARCHAR(24) NOT NULL,
    attempt_number INTEGER NOT NULL,
    response_summary VARCHAR(500),
    next_attempt_at TIMESTAMP WITH TIME ZONE,
    is_test BOOLEAN NOT NULL,
    CONSTRAINT pk_notification_attempts PRIMARY KEY (id),
    CONSTRAINT fk_notification_attempts_alert_instance_id_alert_instances FOREIGN KEY(alert_instance_id) REFERENCES alert_instances (id) ON DELETE CASCADE,
    CONSTRAINT fk_notification_attempts_channel_id_notification_channels FOREIGN KEY(channel_id) REFERENCES notification_channels (id) ON DELETE RESTRICT
);

CREATE INDEX ix_notification_attempts_alert_instance_id ON notification_attempts (alert_instance_id);

ALTER TABLE utility_accounts ADD CONSTRAINT fk_utility_accounts_active_rate_version_id_rate_versions FOREIGN KEY(active_rate_version_id) REFERENCES rate_versions (id) ON DELETE SET NULL;

ALTER TABLE aggregate_members ADD CONSTRAINT fk_aggregate_members_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT;

INSERT INTO alembic_version (version_num) VALUES ('20260720_0001') RETURNING alembic_version.version_num;

-- Running upgrade 20260720_0001 -> 20260720_0002

ALTER TABLE devices ADD COLUMN lifecycle_status VARCHAR(24) DEFAULT 'active' NOT NULL;

ALTER TABLE devices ADD COLUMN lifecycle_generation INTEGER DEFAULT 0 NOT NULL;

ALTER TABLE devices ADD COLUMN decommissioned_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE devices ADD COLUMN decommissioned_by VARCHAR(36);

ALTER TABLE devices ADD CONSTRAINT fk_devices_decommissioned_by_users FOREIGN KEY(decommissioned_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE devices ADD COLUMN decommission_reason VARCHAR(64);

CREATE INDEX ix_devices_lifecycle_status ON devices (lifecycle_status);

CREATE INDEX ix_devices_decommissioned_at ON devices (decommissioned_at);

CREATE INDEX ix_devices_decommissioned_by ON devices (decommissioned_by);

UPDATE devices SET lifecycle_status = 'decommissioned', decommissioned_at = revoked_at, decommission_reason = 'legacy_revoke', lifecycle_generation = 1 WHERE revoked_at IS NOT NULL;

CREATE TABLE device_lifecycle_events (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    generation INTEGER NOT NULL,
    event_type VARCHAR(32) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
    actor_id VARCHAR(36),
    reason VARCHAR(64),
    site_id VARCHAR(36),
    circuit_id VARCHAR(36),
    details JSON NOT NULL,
    CONSTRAINT pk_device_lifecycle_events PRIMARY KEY (id),
    CONSTRAINT fk_device_lifecycle_events_actor_id_users FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_device_lifecycle_events_circuit_id_circuits FOREIGN KEY(circuit_id) REFERENCES circuits (id) ON DELETE SET NULL,
    CONSTRAINT fk_device_lifecycle_events_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT,
    CONSTRAINT fk_device_lifecycle_events_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE SET NULL,
    CONSTRAINT uq_device_lifecycle_generation_event UNIQUE (device_id, generation, event_type)
);

CREATE INDEX ix_device_lifecycle_events_device_id ON device_lifecycle_events (device_id);

CREATE INDEX ix_device_lifecycle_events_event_type ON device_lifecycle_events (event_type);

CREATE INDEX ix_device_lifecycle_events_occurred_at ON device_lifecycle_events (occurred_at);

CREATE INDEX ix_device_lifecycle_events_actor_id ON device_lifecycle_events (actor_id);

CREATE TABLE log_export_jobs (
    id VARCHAR(36) NOT NULL,
    requested_by VARCHAR(36) NOT NULL,
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    services JSON NOT NULL,
    status VARCHAR(24) NOT NULL,
    file_path TEXT,
    size_bytes INTEGER,
    error_code VARCHAR(64),
    completed_at TIMESTAMP WITH TIME ZONE,
    downloaded_at TIMESTAMP WITH TIME ZONE,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    correlation_id VARCHAR(128) NOT NULL,
    CONSTRAINT pk_log_export_jobs PRIMARY KEY (id),
    CONSTRAINT fk_log_export_jobs_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_log_export_jobs_requested_by ON log_export_jobs (requested_by);

CREATE INDEX ix_log_export_jobs_requested_at ON log_export_jobs (requested_at);

CREATE INDEX ix_log_export_jobs_status ON log_export_jobs (status);

CREATE INDEX ix_log_export_jobs_expires_at ON log_export_jobs (expires_at);

CREATE INDEX ix_log_export_jobs_correlation_id ON log_export_jobs (correlation_id);

UPDATE alembic_version SET version_num='20260720_0002' WHERE alembic_version.version_num = '20260720_0001';

-- Running upgrade 20260720_0002 -> 20260720_0003

ALTER TABLE audit_events ADD COLUMN correlation_id VARCHAR(128);

CREATE INDEX ix_audit_events_correlation_id ON audit_events (correlation_id);

ALTER TABLE utility_accounts ADD COLUMN provider_mode VARCHAR(32) DEFAULT 'sce_bundled' NOT NULL;

ALTER TABLE utility_accounts ADD COLUMN cost_scope_default VARCHAR(40) DEFAULT 'energy_only' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN plan_kind VARCHAR(32) DEFAULT 'official_sce' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN ownership_scope VARCHAR(32) DEFAULT 'global' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN owner_site_id VARCHAR(36);

ALTER TABLE rate_plans ADD CONSTRAINT fk_rate_plans_owner_site_id_sites FOREIGN KEY(owner_site_id) REFERENCES sites (id) ON DELETE CASCADE;

ALTER TABLE rate_plans ADD COLUMN owner_utility_account_id VARCHAR(36);

ALTER TABLE rate_plans ADD CONSTRAINT fk_rate_plans_owner_utility_account_id_utility_accounts FOREIGN KEY(owner_utility_account_id) REFERENCES utility_accounts (id) ON DELETE CASCADE;

ALTER TABLE rate_plans ADD COLUMN currency VARCHAR(3) DEFAULT 'USD' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN timezone VARCHAR(64) DEFAULT 'America/Los_Angeles' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN status VARCHAR(24) DEFAULT 'active' NOT NULL;

ALTER TABLE rate_plans ADD COLUMN created_by VARCHAR(36);

ALTER TABLE rate_plans ADD CONSTRAINT fk_rate_plans_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE rate_plans ADD COLUMN cloned_from_rate_version_id VARCHAR(36);

ALTER TABLE rate_plans ADD CONSTRAINT fk_rate_plans_cloned_from_rate_version_id_rate_versions FOREIGN KEY(cloned_from_rate_version_id) REFERENCES rate_versions (id) ON DELETE SET NULL;

CREATE INDEX ix_rate_plans_owner_site_id ON rate_plans (owner_site_id);

CREATE INDEX ix_rate_plans_owner_utility_account_id ON rate_plans (owner_utility_account_id);

ALTER TABLE rate_versions ADD COLUMN status VARCHAR(24) DEFAULT 'draft' NOT NULL;

ALTER TABLE rate_versions ADD COLUMN source_kind VARCHAR(32) DEFAULT 'custom' NOT NULL;

ALTER TABLE rate_versions ADD COLUMN source_checked_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE rate_versions ADD COLUMN source_label VARCHAR(240);

ALTER TABLE rate_versions ADD COLUMN change_summary JSON DEFAULT '{}' NOT NULL;

ALTER TABLE rate_versions ADD COLUMN approved_by VARCHAR(36);

ALTER TABLE rate_versions ADD CONSTRAINT fk_rate_versions_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE rate_versions ADD COLUMN approved_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE rate_versions ADD COLUMN activated_by VARCHAR(36);

ALTER TABLE rate_versions ADD CONSTRAINT fk_rate_versions_activated_by_users FOREIGN KEY(activated_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE rate_versions ADD COLUMN activated_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE rate_versions ADD COLUMN normalized_payload JSON;

ALTER TABLE rate_versions ADD COLUMN automatically_activated BOOLEAN DEFAULT false NOT NULL;

CREATE INDEX ix_rate_versions_status ON rate_versions (status);

UPDATE rate_versions SET status = CASE WHEN is_active THEN 'active' ELSE 'retired' END, source_kind = 'official_sce', source_checked_at = source_checked_on::timestamp with time zone;

ALTER TABLE rate_seasons ADD COLUMN priority INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE rate_seasons ADD COLUMN leap_day_behavior VARCHAR(32) DEFAULT 'include' NOT NULL;

ALTER TABLE rate_periods ADD COLUMN delivery_per_kwh NUMERIC(14, 8) DEFAULT '0' NOT NULL;

ALTER TABLE rate_periods ADD COLUMN generation_per_kwh NUMERIC(14, 8) DEFAULT '0' NOT NULL;

ALTER TABLE rate_periods ADD COLUMN adjustment_per_kwh NUMERIC(14, 8) DEFAULT '0' NOT NULL;

ALTER TABLE rate_periods ADD COLUMN display_order INTEGER DEFAULT '0' NOT NULL;

UPDATE rate_periods SET delivery_per_kwh = price_per_kwh;

ALTER TABLE rate_adjustments ADD COLUMN unit VARCHAR(32) DEFAULT 'per_kwh' NOT NULL;

ALTER TABLE rate_adjustments ADD COLUMN scope VARCHAR(40) DEFAULT 'all_energy' NOT NULL;

ALTER TABLE rate_adjustments ADD COLUMN eligibility JSON DEFAULT '{}' NOT NULL;

ALTER TABLE rate_adjustments ADD COLUMN effective_from DATE;

ALTER TABLE rate_adjustments ADD COLUMN effective_to DATE;

ALTER TABLE rate_adjustments ADD COLUMN display_order INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE rate_adjustments ADD COLUMN description TEXT DEFAULT '' NOT NULL;

ALTER TABLE cost_interval_results ADD COLUMN adjustment_breakdown JSON DEFAULT '{}' NOT NULL;

ALTER TABLE cost_interval_results ADD COLUMN calculation_version VARCHAR(40) DEFAULT 'rate-engine/1' NOT NULL;

CREATE TABLE rate_sync_configuration (
    id VARCHAR(36) NOT NULL,
    enabled BOOLEAN NOT NULL,
    schedule_cron VARCHAR(64) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    jitter_minutes INTEGER NOT NULL,
    approval_mode VARCHAR(32) NOT NULL,
    auto_activate_verified BOOLEAN NOT NULL,
    last_scheduled_for TIMESTAMP WITH TIME ZONE,
    next_scheduled_run TIMESTAMP WITH TIME ZONE,
    last_attempted_run TIMESTAMP WITH TIME ZONE,
    last_successful_run TIMESTAMP WITH TIME ZONE,
    last_source_change TIMESTAMP WITH TIME ZONE,
    last_candidate_created TIMESTAMP WITH TIME ZONE,
    last_approved_version TIMESTAMP WITH TIME ZONE,
    last_error TEXT,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_by VARCHAR(36),
    CONSTRAINT pk_rate_sync_configuration PRIMARY KEY (id),
    CONSTRAINT fk_rate_sync_configuration_updated_by_users FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE rate_sources (
    id VARCHAR(36) NOT NULL,
    name VARCHAR(160) NOT NULL,
    url VARCHAR(500) NOT NULL,
    parser_id VARCHAR(80) NOT NULL,
    enabled BOOLEAN NOT NULL,
    etag VARCHAR(500),
    last_modified VARCHAR(200),
    last_checked_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    consecutive_failures INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_sources PRIMARY KEY (id),
    CONSTRAINT uq_rate_sources_url UNIQUE (url)
);

CREATE TABLE background_jobs (
    id VARCHAR(36) NOT NULL,
    job_type VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    requested_by VARCHAR(36),
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    scheduled_for TIMESTAMP WITH TIME ZONE,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    correlation_id VARCHAR(128) NOT NULL,
    progress JSON NOT NULL,
    result JSON NOT NULL,
    error_code VARCHAR(80),
    error_detail TEXT,
    CONSTRAINT pk_background_jobs PRIMARY KEY (id),
    CONSTRAINT fk_background_jobs_requested_by_users FOREIGN KEY(requested_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_background_jobs_job_type ON background_jobs (job_type);

CREATE INDEX ix_background_jobs_status ON background_jobs (status);

CREATE INDEX ix_background_jobs_requested_at ON background_jobs (requested_at);

CREATE INDEX ix_background_jobs_scheduled_for ON background_jobs (scheduled_for);

CREATE INDEX ix_background_jobs_correlation_id ON background_jobs (correlation_id);

CREATE TABLE rate_source_checks (
    id VARCHAR(36) NOT NULL,
    job_id VARCHAR(36) NOT NULL,
    rate_source_id VARCHAR(36) NOT NULL,
    checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
    http_status INTEGER,
    outcome VARCHAR(32) NOT NULL,
    final_url VARCHAR(500),
    etag VARCHAR(500),
    last_modified VARCHAR(200),
    duration_ms INTEGER,
    response_bytes INTEGER,
    error_code VARCHAR(80),
    error_detail TEXT,
    CONSTRAINT pk_rate_source_checks PRIMARY KEY (id),
    CONSTRAINT fk_rate_source_checks_job_id_background_jobs FOREIGN KEY(job_id) REFERENCES background_jobs (id) ON DELETE CASCADE,
    CONSTRAINT fk_rate_source_checks_rate_source_id_rate_sources FOREIGN KEY(rate_source_id) REFERENCES rate_sources (id) ON DELETE RESTRICT
);

CREATE INDEX ix_rate_source_checks_job_id ON rate_source_checks (job_id);

CREATE INDEX ix_rate_source_checks_rate_source_id ON rate_source_checks (rate_source_id);

CREATE INDEX ix_rate_source_checks_checked_at ON rate_source_checks (checked_at);

CREATE INDEX ix_rate_source_checks_outcome ON rate_source_checks (outcome);

CREATE TABLE rate_source_artifacts (
    id VARCHAR(36) NOT NULL,
    source_check_id VARCHAR(36) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    content_type VARCHAR(160) NOT NULL,
    byte_size INTEGER NOT NULL,
    storage_path VARCHAR(1000) NOT NULL,
    original_filename VARCHAR(255),
    captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_source_artifacts PRIMARY KEY (id),
    CONSTRAINT fk_rate_source_artifacts_source_check_id_rate_source_checks FOREIGN KEY(source_check_id) REFERENCES rate_source_checks (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_source_artifacts_source_check_id ON rate_source_artifacts (source_check_id);

CREATE INDEX ix_rate_source_artifacts_sha256 ON rate_source_artifacts (sha256);

CREATE INDEX ix_rate_source_artifacts_captured_at ON rate_source_artifacts (captured_at);

CREATE TABLE rate_extraction_results (
    id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    parser_id VARCHAR(80) NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    status VARCHAR(32) NOT NULL,
    normalized_payload JSON,
    warnings JSON NOT NULL,
    errors JSON NOT NULL,
    extracted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_extraction_results PRIMARY KEY (id),
    CONSTRAINT fk_rate_extraction_results_artifact_id_rate_source_artifacts FOREIGN KEY(artifact_id) REFERENCES rate_source_artifacts (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_extraction_results_artifact_id ON rate_extraction_results (artifact_id);

CREATE INDEX ix_rate_extraction_results_status ON rate_extraction_results (status);

CREATE TABLE rate_change_candidates (
    id VARCHAR(36) NOT NULL,
    rate_plan_id VARCHAR(36),
    extraction_result_id VARCHAR(36) NOT NULL,
    base_rate_version_id VARCHAR(36),
    candidate_rate_version_id VARCHAR(36),
    status VARCHAR(32) NOT NULL,
    risk_level VARCHAR(24) NOT NULL,
    summary JSON NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    reviewed_by VARCHAR(36),
    CONSTRAINT pk_rate_change_candidates PRIMARY KEY (id),
    CONSTRAINT fk_rate_change_candidates_rate_plan_id_rate_plans FOREIGN KEY(rate_plan_id) REFERENCES rate_plans (id) ON DELETE SET NULL,
    CONSTRAINT fk_rate_change_candidates_extraction_result_id_rate_ext_38c9 FOREIGN KEY(extraction_result_id) REFERENCES rate_extraction_results (id) ON DELETE RESTRICT,
    CONSTRAINT fk_rate_change_candidates_base_rate_version_id_rate_versions FOREIGN KEY(base_rate_version_id) REFERENCES rate_versions (id) ON DELETE SET NULL,
    CONSTRAINT fk_rate_change_candidates_candidate_rate_version_id_rat_7cf6 FOREIGN KEY(candidate_rate_version_id) REFERENCES rate_versions (id) ON DELETE SET NULL,
    CONSTRAINT fk_rate_change_candidates_reviewed_by_users FOREIGN KEY(reviewed_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_rate_change_candidates_rate_plan_id ON rate_change_candidates (rate_plan_id);

CREATE INDEX ix_rate_change_candidates_extraction_result_id ON rate_change_candidates (extraction_result_id);

CREATE INDEX ix_rate_change_candidates_status ON rate_change_candidates (status);

CREATE INDEX ix_rate_change_candidates_created_at ON rate_change_candidates (created_at);

CREATE TABLE rate_candidate_differences (
    id VARCHAR(36) NOT NULL,
    candidate_id VARCHAR(36) NOT NULL,
    path VARCHAR(500) NOT NULL,
    change_type VARCHAR(24) NOT NULL,
    before_value JSON,
    after_value JSON,
    material BOOLEAN NOT NULL,
    CONSTRAINT pk_rate_candidate_differences PRIMARY KEY (id),
    CONSTRAINT fk_rate_candidate_differences_candidate_id_rate_change__e7ac FOREIGN KEY(candidate_id) REFERENCES rate_change_candidates (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_candidate_differences_candidate_id ON rate_candidate_differences (candidate_id);

CREATE TABLE rate_approval_decisions (
    id VARCHAR(36) NOT NULL,
    candidate_id VARCHAR(36) NOT NULL,
    decision VARCHAR(24) NOT NULL,
    comment TEXT NOT NULL,
    decided_by VARCHAR(36) NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_approval_decisions PRIMARY KEY (id),
    CONSTRAINT fk_rate_approval_decisions_candidate_id_rate_change_candidates FOREIGN KEY(candidate_id) REFERENCES rate_change_candidates (id) ON DELETE CASCADE,
    CONSTRAINT fk_rate_approval_decisions_decided_by_users FOREIGN KEY(decided_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_rate_approval_decisions_candidate_id ON rate_approval_decisions (candidate_id);

CREATE INDEX ix_rate_approval_decisions_decided_at ON rate_approval_decisions (decided_at);

CREATE TABLE rate_version_sources (
    rate_version_id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    extraction_result_id VARCHAR(36),
    relationship VARCHAR(32) NOT NULL,
    CONSTRAINT pk_rate_version_sources PRIMARY KEY (rate_version_id, artifact_id),
    CONSTRAINT fk_rate_version_sources_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE,
    CONSTRAINT fk_rate_version_sources_artifact_id_rate_source_artifacts FOREIGN KEY(artifact_id) REFERENCES rate_source_artifacts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_rate_version_sources_extraction_result_id_rate_extra_4f85 FOREIGN KEY(extraction_result_id) REFERENCES rate_extraction_results (id) ON DELETE SET NULL
);

CREATE TABLE rate_assignments (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to TIMESTAMP WITH TIME ZONE,
    assigned_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_rate_assignments PRIMARY KEY (id),
    CONSTRAINT fk_rate_assignments_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE CASCADE,
    CONSTRAINT fk_rate_assignments_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_rate_assignments_assigned_by_users FOREIGN KEY(assigned_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_rate_assignments_utility_account_id ON rate_assignments (utility_account_id);

CREATE INDEX ix_rate_assignments_rate_version_id ON rate_assignments (rate_version_id);

CREATE INDEX ix_rate_assignments_effective_from ON rate_assignments (effective_from);

INSERT INTO rate_sync_configuration (id, enabled, schedule_cron, timezone, jitter_minutes, approval_mode, auto_activate_verified, updated_at) VALUES ('default', true, '15 3 * * 0', 'America/Los_Angeles', 20, 'manual_review', false, now());

UPDATE alembic_version SET version_num='20260720_0003' WHERE alembic_version.version_num = '20260720_0002';

-- Running upgrade 20260720_0003 -> 20260720_0004

ALTER TABLE rate_sources ADD COLUMN effective_from_hint DATE;

ALTER TABLE rate_sources ADD COLUMN created_by VARCHAR(36);

ALTER TABLE rate_sources ADD CONSTRAINT fk_rate_sources_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

UPDATE rate_sources SET effective_from_hint = DATE '2026-06-01' WHERE url = 'https://www.sce.com/save-money/rates-financing/residential-rate-plans/time-of-use-plans';

UPDATE alembic_version SET version_num='20260720_0004' WHERE alembic_version.version_num = '20260720_0003';

-- Running upgrade 20260720_0004 -> 20260720_0005

ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD COLUMN all_sites BOOLEAN DEFAULT true NOT NULL;

ALTER TABLE users ADD COLUMN access_revision INTEGER DEFAULT '1' NOT NULL;

ALTER TABLE sessions ADD COLUMN reauthenticated_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE roles ADD COLUMN display_name VARCHAR(120) DEFAULT '' NOT NULL;

ALTER TABLE roles ADD COLUMN is_builtin BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE roles ADD COLUMN is_archived BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE roles ADD COLUMN revision INTEGER DEFAULT '1' NOT NULL;

ALTER TABLE roles ADD COLUMN created_by VARCHAR(36);

ALTER TABLE roles ADD COLUMN updated_by VARCHAR(36);

ALTER TABLE roles ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE roles ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE roles ADD CONSTRAINT fk_roles_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE roles ADD CONSTRAINT fk_roles_updated_by_users FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX ix_roles_is_builtin ON roles (is_builtin);

CREATE INDEX ix_roles_is_archived ON roles (is_archived);

INSERT INTO roles (name, description) SELECT 'admin', 'Full application administration' WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'admin');

INSERT INTO roles (name, description) SELECT 'operator', 'Assigned-site device and alert operations' WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'operator');

INSERT INTO roles (name, description) SELECT 'rate-manager', 'Rate plan and source administration' WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'rate-manager');

INSERT INTO roles (name, description) SELECT 'viewer', 'Read-only assigned-site dashboard access' WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'viewer');

UPDATE roles SET display_name = name WHERE display_name = '';

UPDATE roles SET display_name = 'Administrator', is_builtin = true WHERE name = 'admin';

UPDATE roles SET display_name = 'Operator', is_builtin = true WHERE name = 'operator';

UPDATE roles SET display_name = 'Rate Manager', is_builtin = true WHERE name = 'rate-manager';

UPDATE roles SET display_name = 'Regular User / Read-Only Viewer', is_builtin = true WHERE name = 'viewer';

CREATE UNIQUE INDEX uq_roles_display_name_lower ON roles (lower(display_name));

CREATE TABLE permissions (
    code VARCHAR(80) NOT NULL,
    group_name VARCHAR(80) NOT NULL,
    label VARCHAR(120) NOT NULL,
    description VARCHAR(500) NOT NULL,
    high_risk BOOLEAN DEFAULT false NOT NULL,
    CONSTRAINT pk_permissions PRIMARY KEY (code)
);

CREATE INDEX ix_permissions_group_name ON permissions (group_name);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('overview.view', 'overview', 'overview.view', 'overview.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('usage.view', 'usage', 'usage.view', 'usage.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('history.view', 'history', 'history.view', 'history.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('history.export', 'history', 'history.export', 'history.export', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('costs.view', 'costs', 'costs.view', 'costs.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('costs.export', 'costs', 'costs.export', 'costs.export', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.view', 'sites', 'sites.view', 'sites.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.manage', 'sites', 'sites.manage', 'sites.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('topology.view', 'topology', 'topology.view', 'topology.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('topology.manage', 'topology', 'topology.manage', 'topology.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('devices.view', 'devices', 'devices.view', 'devices.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('devices.manage', 'devices', 'devices.manage', 'devices.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('devices.remove', 'devices', 'devices.remove', 'devices.remove', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('enrollment.view', 'enrollment', 'enrollment.view', 'enrollment.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('enrollment.manage', 'enrollment', 'enrollment.manage', 'enrollment.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('firmware.view', 'firmware', 'firmware.view', 'firmware.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('firmware.manage', 'firmware', 'firmware.manage', 'firmware.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.view', 'rates', 'rates.view', 'rates.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.manage_custom', 'rates', 'rates.manage_custom', 'rates.manage_custom', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.manage_sources', 'rates', 'rates.manage_sources', 'rates.manage_sources', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.check_sources', 'rates', 'rates.check_sources', 'rates.check_sources', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.review_candidates', 'rates', 'rates.review_candidates', 'rates.review_candidates', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.approve_candidates', 'rates', 'rates.approve_candidates', 'rates.approve_candidates', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('rates.assign', 'rates', 'rates.assign', 'rates.assign', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('alerts.view', 'alerts', 'alerts.view', 'alerts.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('alerts.acknowledge', 'alerts', 'alerts.acknowledge', 'alerts.acknowledge', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('alerts.manage_rules', 'alerts', 'alerts.manage_rules', 'alerts.manage_rules', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('alerts.manage_delivery', 'alerts', 'alerts.manage_delivery', 'alerts.manage_delivery', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('backups.view', 'backups', 'backups.view', 'backups.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('backups.create', 'backups', 'backups.create', 'backups.create', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('backups.restore', 'backups', 'backups.restore', 'backups.restore', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('logs.export', 'logs', 'logs.export', 'logs.export', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.view', 'users', 'users.view', 'users.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.manage', 'users', 'users.manage', 'users.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.manage_protected', 'users', 'users.manage_protected', 'users.manage_protected', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('roles.view', 'roles', 'roles.view', 'roles.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('roles.manage', 'roles', 'roles.manage', 'roles.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('audit.view', 'audit', 'audit.view', 'audit.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('settings.view', 'settings', 'settings.view', 'settings.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('settings.manage', 'settings', 'settings.manage', 'settings.manage', true);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('interface_text.view', 'interface_text', 'interface_text.view', 'interface_text.view', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('interface_text.manage', 'interface_text', 'interface_text.manage', 'interface_text.manage', true);

CREATE TABLE role_permissions (
    role_name VARCHAR(32) NOT NULL,
    permission_code VARCHAR(80) NOT NULL,
    CONSTRAINT pk_role_permissions PRIMARY KEY (role_name, permission_code),
    CONSTRAINT fk_role_permissions_role_name_roles FOREIGN KEY(role_name) REFERENCES roles (name) ON DELETE CASCADE,
    CONSTRAINT fk_role_permissions_permission_code_permissions FOREIGN KEY(permission_code) REFERENCES permissions (code) ON DELETE RESTRICT
);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'alerts.acknowledge');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'alerts.manage_delivery');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'alerts.manage_rules');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'alerts.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'audit.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'backups.create');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'backups.restore');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'backups.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'costs.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'costs.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'devices.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'devices.remove');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'devices.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'enrollment.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'enrollment.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'firmware.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'firmware.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'history.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'history.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'interface_text.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'interface_text.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'logs.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'overview.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.approve_candidates');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.assign');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.check_sources');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.manage_custom');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.manage_sources');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.review_candidates');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'rates.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'roles.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'roles.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'settings.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'settings.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'sites.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'sites.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'topology.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'topology.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'usage.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.manage_protected');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'alerts.acknowledge');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'alerts.manage_rules');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'alerts.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'costs.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'costs.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'devices.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'devices.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'enrollment.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'enrollment.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'firmware.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'history.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'history.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'overview.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'rates.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'sites.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'topology.manage');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'topology.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'usage.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'alerts.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'costs.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'costs.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'devices.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'history.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'history.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'overview.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.approve_candidates');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.assign');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.check_sources');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.manage_custom');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.manage_sources');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.review_candidates');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'rates.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'sites.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'topology.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'usage.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'alerts.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'costs.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'costs.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'devices.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'history.export');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'history.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'overview.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'rates.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'sites.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'topology.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'usage.view');

CREATE TABLE user_sites (
    user_id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    CONSTRAINT pk_user_sites PRIMARY KEY (user_id, site_id),
    CONSTRAINT fk_user_sites_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_sites_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE CASCADE
);

CREATE TABLE role_revisions (
    id VARCHAR(36) NOT NULL,
    role_name VARCHAR(32) NOT NULL,
    revision INTEGER NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    description VARCHAR(255) NOT NULL,
    permissions JSON NOT NULL,
    created_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reason VARCHAR(500),
    CONSTRAINT pk_role_revisions PRIMARY KEY (id),
    CONSTRAINT uq_role_revision UNIQUE (role_name, revision),
    CONSTRAINT fk_role_revisions_role_name_roles FOREIGN KEY(role_name) REFERENCES roles (name) ON DELETE RESTRICT,
    CONSTRAINT fk_role_revisions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_role_revisions_role_name ON role_revisions (role_name);

CREATE TABLE interface_text_revisions (
    id VARCHAR(36) NOT NULL,
    revision INTEGER NOT NULL,
    values JSON NOT NULL,
    created_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reason VARCHAR(500),
    restored_from_id VARCHAR(36),
    CONSTRAINT pk_interface_text_revisions PRIMARY KEY (id),
    CONSTRAINT uq_interface_text_revisions_revision UNIQUE (revision),
    CONSTRAINT fk_interface_text_revisions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_interface_text_revisions_restored_from_id_interface__2109 FOREIGN KEY(restored_from_id) REFERENCES interface_text_revisions (id) ON DELETE SET NULL
);

CREATE INDEX ix_interface_text_revisions_revision ON interface_text_revisions (revision);

CREATE INDEX ix_interface_text_revisions_created_by ON interface_text_revisions (created_by);

CREATE TABLE interface_text_drafts (
    id VARCHAR(36) NOT NULL,
    base_revision INTEGER DEFAULT '0' NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    previewed_revision INTEGER,
    values JSON NOT NULL,
    edited_by VARCHAR(36),
    reason VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_interface_text_drafts PRIMARY KEY (id),
    CONSTRAINT fk_interface_text_drafts_edited_by_users FOREIGN KEY(edited_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_interface_text_drafts_edited_by ON interface_text_drafts (edited_by);

CREATE TABLE interface_text_state (
    id VARCHAR(36) NOT NULL,
    current_revision_id VARCHAR(36),
    current_revision INTEGER DEFAULT '0' NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_interface_text_state PRIMARY KEY (id),
    CONSTRAINT fk_interface_text_state_current_revision_id_interface_t_285a FOREIGN KEY(current_revision_id) REFERENCES interface_text_revisions (id) ON DELETE RESTRICT
);

INSERT INTO interface_text_state (id, current_revision_id, current_revision, updated_at) VALUES ('current', NULL, 0, CURRENT_TIMESTAMP);

UPDATE alembic_version SET version_num='20260720_0005' WHERE alembic_version.version_num = '20260720_0004';

-- Running upgrade 20260720_0005 -> 20260720_0006

CREATE TABLE status_layout_revisions (
    id VARCHAR(36) NOT NULL,
    revision INTEGER NOT NULL,
    registry_version VARCHAR(64) NOT NULL,
    configuration JSON NOT NULL,
    created_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reason VARCHAR(500),
    restored_from_id VARCHAR(36),
    CONSTRAINT pk_status_layout_revisions PRIMARY KEY (id),
    CONSTRAINT uq_status_layout_revisions_revision UNIQUE (revision),
    CONSTRAINT fk_status_layout_revisions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_status_layout_revisions_restored_from_id_status_layo_3f9a FOREIGN KEY(restored_from_id) REFERENCES status_layout_revisions (id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX ix_status_layout_revisions_revision ON status_layout_revisions (revision);

CREATE INDEX ix_status_layout_revisions_created_by ON status_layout_revisions (created_by);

CREATE INDEX ix_status_layout_revisions_created_at ON status_layout_revisions (created_at);

CREATE TABLE status_layout_drafts (
    id VARCHAR(36) NOT NULL,
    base_revision INTEGER DEFAULT '1' NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    previewed_revision INTEGER,
    registry_version VARCHAR(64) NOT NULL,
    configuration JSON NOT NULL,
    edited_by VARCHAR(36),
    reason VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_status_layout_drafts PRIMARY KEY (id),
    CONSTRAINT fk_status_layout_drafts_edited_by_users FOREIGN KEY(edited_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_status_layout_drafts_edited_by ON status_layout_drafts (edited_by);

CREATE INDEX ix_status_layout_drafts_updated_at ON status_layout_drafts (updated_at);

CREATE TABLE status_layout_state (
    id VARCHAR(36) NOT NULL,
    current_revision_id VARCHAR(36),
    current_revision INTEGER DEFAULT '1' NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_status_layout_state PRIMARY KEY (id),
    CONSTRAINT fk_status_layout_state_current_revision_id_status_layou_8b84 FOREIGN KEY(current_revision_id) REFERENCES status_layout_revisions (id) ON DELETE RESTRICT
);

INSERT INTO status_layout_revisions (id, revision, registry_version, configuration, created_by, created_at, reason, restored_from_id) VALUES ('00000000-0000-4000-8000-000000000006', 1, 'status-indicators/1.0', json_build_object('schema_version', 'power-monitor-status-layout/1.0', 'registry_version', 'status-indicators/1.0', 'personalization_enabled', false, 'items', json_build_array()), NULL, CURRENT_TIMESTAMP, 'Compiled dashboard layout captured during migration', NULL);

INSERT INTO status_layout_state (id, current_revision_id, current_revision, updated_at) VALUES ('current', '00000000-0000-4000-8000-000000000006', 1, CURRENT_TIMESTAMP);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('status_indicators.view', 'Administration', 'View status layouts', 'View registered indicators and the effective published layout.', false);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('status_indicators.manage', 'Administration', 'Manage status layouts', 'Draft, preview, publish, import, reset, and restore status layouts.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'status_indicators.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('operator', 'status_indicators.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('rate-manager', 'status_indicators.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('viewer', 'status_indicators.view');

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'status_indicators.manage');

UPDATE alembic_version SET version_num='20260720_0006' WHERE alembic_version.version_num = '20260720_0005';

-- Running upgrade 20260720_0006 -> 20260721_0007

INSERT INTO status_layout_revisions
            (id, revision, registry_version, configuration, created_by, created_at,
             reason, restored_from_id)
        SELECT
            '00000000-0000-4000-8000-000000000007',
            state.current_revision + 1,
            current.registry_version,
            jsonb_set(
                current.configuration::jsonb,
                '{items}',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            CASE
                                WHEN item->>'indicator_key' IN
                                    ('system.api_health', 'system.database_health',
                                     'system.worker_health')
                                    THEN item || jsonb_build_object(
                                        'page', 'system_health',
                                        'zone', 'diagnostics_summary'
                                    )
                                WHEN item->>'indicator_key' = 'site.current'
                                    THEN item || jsonb_build_object('visible', false)
                                ELSE item
                            END
                        )
                        FROM jsonb_array_elements(
                            COALESCE(current.configuration::jsonb->'items', '[]'::jsonb)
                        ) AS item
                        WHERE NOT (
                            item->>'indicator_key' IN
                                ('data.current_power', 'data.aggregate_coverage')
                            AND item->>'page' IN ('overview', 'history')
                            AND COALESCE(item->>'role', '*') = '*'
                            AND COALESCE(item->>'breakpoint', 'default') = 'default'
                        )
                    ),
                    '[]'::jsonb
                ) || jsonb_build_array(
                    jsonb_build_object(
                        'indicator_key', 'data.current_power',
                        'page', 'overview', 'role', '*', 'breakpoint', 'default',
                        'visible', false
                    ),
                    jsonb_build_object(
                        'indicator_key', 'data.aggregate_coverage',
                        'page', 'history', 'role', '*', 'breakpoint', 'default',
                        'visible', false
                    )
                ),
                true
            )::json,
            NULL,
            CURRENT_TIMESTAMP,
            'System migration: compact shell, diagnostics relocation, and metric deduplication',
            state.current_revision_id
        FROM status_layout_state AS state
        JOIN status_layout_revisions AS current ON current.id = state.current_revision_id
        WHERE state.id = 'current';

UPDATE status_layout_state
        SET current_revision_id = '00000000-0000-4000-8000-000000000007',
            current_revision = current_revision + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 'current';

INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        VALUES
            ('00000000-0000-4000-9000-000000000007', CURRENT_TIMESTAMP, 'system', NULL,
             'status_layout.information_architecture_migrated', 'status_layout',
             '00000000-0000-4000-8000-000000000007', NULL, 'success',
             'migration:20260721_0007',
             json_build_object(
                 'summary', 'Moved system health to diagnostics and repaired canonical placements',
                 'previous_revision_preserved', true,
                 'automatic_repair', 'Keep recommended placement'
             ));

UPDATE alembic_version SET version_num='20260721_0007' WHERE alembic_version.version_num = '20260720_0006';

-- Running upgrade 20260721_0007 -> 20260721_0008

ALTER TABLE utility_accounts ADD COLUMN nickname VARCHAR(160);

ALTER TABLE utility_accounts ADD COLUMN account_number_suffix VARCHAR(8);

ALTER TABLE utility_accounts ADD COLUMN status VARCHAR(24) DEFAULT 'active' NOT NULL;

ALTER TABLE utility_accounts ADD COLUMN service_class VARCHAR(80);

ALTER TABLE utility_accounts ADD COLUMN allocation_method VARCHAR(80);

ALTER TABLE utility_accounts ADD COLUMN full_account_override BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE utility_accounts ADD COLUMN adjustment_config JSON DEFAULT '{}' NOT NULL;

ALTER TABLE utility_accounts ADD COLUMN revision INTEGER DEFAULT '1' NOT NULL;

ALTER TABLE utility_accounts ADD COLUMN archived_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE utility_accounts ADD COLUMN archived_by VARCHAR(36);

ALTER TABLE utility_accounts ADD CONSTRAINT fk_utility_accounts_archived_by_users FOREIGN KEY(archived_by) REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX ix_utility_accounts_status ON utility_accounts (status);

CREATE INDEX ix_utility_accounts_archived_at ON utility_accounts (archived_at);

ALTER TABLE utility_accounts ADD CONSTRAINT ck_utility_accounts_utility_account_status CHECK (status IN ('active','archived'));

ALTER TABLE utility_accounts ADD CONSTRAINT ck_utility_accounts_utility_account_cost_scope CHECK (cost_scope_default IN ('energy_only','allocated_account_estimate','full_account_estimate'));

ALTER TABLE rate_assignments ADD COLUMN assignment_reason VARCHAR(500);

ALTER TABLE rate_assignments ADD CONSTRAINT ck_rate_assignments_rate_assignment_effective_window CHECK (effective_to IS NULL OR effective_to > effective_from);

CREATE INDEX ix_rate_assignments_account_window ON rate_assignments (utility_account_id, effective_from, effective_to);

CREATE TABLE utility_account_adjustments (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    component VARCHAR(48) NOT NULL,
    value NUMERIC(18, 8) NOT NULL,
    unit VARCHAR(24) NOT NULL,
    provenance VARCHAR(240) NOT NULL,
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to TIMESTAMP WITH TIME ZONE,
    enabled BOOLEAN DEFAULT true NOT NULL,
    created_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utility_account_adjustments PRIMARY KEY (id),
    CONSTRAINT ck_utility_account_adjustments_utility_adjustment_component CHECK (component IN ('cca_generation','direct_access','baseline_credit','service_charge','tax_fee','custom_fixed','custom_per_kwh')),
    CONSTRAINT ck_utility_account_adjustments_adjustment_unit CHECK (unit IN ('per_kwh','fixed','percent','included')),
    CONSTRAINT ck_utility_account_adjustments_adjustment_effective_window CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT fk_utility_account_adjustments_utility_account_id_utili_cc20 FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_account_adjustments_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_account_adjustments_utility_account_id ON utility_account_adjustments (utility_account_id);

CREATE INDEX ix_utility_account_adjustments_effective_from ON utility_account_adjustments (effective_from);

CREATE TABLE sensor_network_policies (
    id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    direction VARCHAR(24) NOT NULL,
    mode VARCHAR(40) NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    migration_notice_pending BOOLEAN DEFAULT true NOT NULL,
    migrated_from_legacy BOOLEAN DEFAULT true NOT NULL,
    updated_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_sensor_network_policies PRIMARY KEY (id),
    CONSTRAINT uq_sensor_policy_site_direction UNIQUE (site_id, direction),
    CONSTRAINT ck_sensor_network_policies_sensor_policy_direction CHECK (direction IN ('device_ingress','server_pull')),
    CONSTRAINT ck_sensor_network_policies_sensor_policy_mode CHECK (mode IN ('allow_listed_private','allow_all_private','deny_all','legacy_authenticated_any','legacy_public_and_listed')),
    CONSTRAINT fk_sensor_network_policies_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE CASCADE,
    CONSTRAINT fk_sensor_network_policies_updated_by_users FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_sensor_network_policies_site_id ON sensor_network_policies (site_id);

CREATE TABLE sensor_network_cidrs (
    id VARCHAR(36) NOT NULL,
    policy_id VARCHAR(36) NOT NULL,
    network VARCHAR(80) NOT NULL,
    label VARCHAR(120) NOT NULL,
    enabled BOOLEAN DEFAULT true NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT pk_sensor_network_cidrs PRIMARY KEY (id),
    CONSTRAINT uq_policy_network UNIQUE (policy_id, network),
    CONSTRAINT fk_sensor_network_cidrs_policy_id_sensor_network_policies FOREIGN KEY(policy_id) REFERENCES sensor_network_policies (id) ON DELETE CASCADE
);

CREATE INDEX ix_sensor_network_cidrs_policy_id ON sensor_network_cidrs (policy_id);

CREATE TABLE network_policy_revisions (
    id VARCHAR(36) NOT NULL,
    policy_id VARCHAR(36) NOT NULL,
    revision INTEGER NOT NULL,
    mode VARCHAR(40) NOT NULL,
    cidrs JSON NOT NULL,
    changed_by VARCHAR(36),
    changed_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reason VARCHAR(500),
    CONSTRAINT pk_network_policy_revisions PRIMARY KEY (id),
    CONSTRAINT uq_policy_revision UNIQUE (policy_id, revision),
    CONSTRAINT fk_network_policy_revisions_policy_id_sensor_network_policies FOREIGN KEY(policy_id) REFERENCES sensor_network_policies (id) ON DELETE CASCADE,
    CONSTRAINT fk_network_policy_revisions_changed_by_users FOREIGN KEY(changed_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_network_policy_revisions_policy_id ON network_policy_revisions (policy_id);

INSERT INTO sensor_network_policies
            (id, site_id, direction, mode, revision, migration_notice_pending,
             migrated_from_legacy, created_at, updated_at)
        SELECT substr(md5(sites.id || chr(58) || 'device_ingress'),1,8)||'-'||substr(md5(sites.id || chr(58) || 'device_ingress'),9,4)||'-4'||substr(md5(sites.id || chr(58) || 'device_ingress'),14,3)||'-8'||substr(md5(sites.id || chr(58) || 'device_ingress'),18,3)||'-'||substr(md5(sites.id || chr(58) || 'device_ingress'),21,12), sites.id, 'device_ingress', 'legacy_authenticated_any', 1,
               true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites;

INSERT INTO sensor_network_policies
            (id, site_id, direction, mode, revision, migration_notice_pending,
             migrated_from_legacy, created_at, updated_at)
        SELECT substr(md5(sites.id || chr(58) || 'server_pull'),1,8)||'-'||substr(md5(sites.id || chr(58) || 'server_pull'),9,4)||'-4'||substr(md5(sites.id || chr(58) || 'server_pull'),14,3)||'-8'||substr(md5(sites.id || chr(58) || 'server_pull'),18,3)||'-'||substr(md5(sites.id || chr(58) || 'server_pull'),21,12), sites.id, 'server_pull',
               CASE
                   WHEN sites.allow_public_polling THEN 'legacy_public_and_listed'
                   WHEN json_array_length(sites.allowed_cidrs) > 0 THEN 'allow_listed_private'
                   ELSE 'deny_all'
               END,
               1, true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites;

INSERT INTO sensor_network_cidrs
            (id, policy_id, network, label, enabled, revision, created_at, updated_at)
        SELECT substr(md5(policy.id || ':' || cidr.value),1,8)||'-'||substr(md5(policy.id || ':' || cidr.value),9,4)||'-4'||substr(md5(policy.id || ':' || cidr.value),14,3)||'-8'||substr(md5(policy.id || ':' || cidr.value),18,3)||'-'||substr(md5(policy.id || ':' || cidr.value),21,12), policy.id, cidr.value, 'Migrated site CIDR', true, 1,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites
        JOIN sensor_network_policies AS policy
          ON policy.site_id = sites.id AND policy.direction = 'server_pull'
        CROSS JOIN LATERAL json_array_elements_text(sites.allowed_cidrs) AS cidr(value);

INSERT INTO network_policy_revisions
            (id, policy_id, revision, mode, cidrs, changed_by, changed_at, reason)
        SELECT policy.id, policy.id, 1, policy.mode, COALESCE(
            (SELECT json_agg(json_build_object('network', cidr.network, 'label', cidr.label,
                                               'enabled', cidr.enabled))
             FROM sensor_network_cidrs AS cidr WHERE cidr.policy_id = policy.id),
            '[]'::json
        ), NULL, CURRENT_TIMESTAMP,
        'System migration preserved the previously effective network behavior.'
        FROM sensor_network_policies AS policy;

INSERT INTO alert_rules
            (id, name, rule_type, severity, enabled, site_id, device_id,
             debounce_seconds, resolve_seconds, configuration, created_at, updated_at)
        SELECT substr(md5('device_address_outside_policy'),1,8)||'-'||substr(md5('device_address_outside_policy'),9,4)||'-4'||substr(md5('device_address_outside_policy'),14,3)||'-8'||substr(md5('device_address_outside_policy'),18,3)||'-'||substr(md5('device_address_outside_policy'),21,12), 'Device address outside server-pull policy',
               'device_address_outside_policy', 'warning', true, NULL, NULL,
               0, 0, '{}'::json, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM alert_rules
            WHERE rule_type = 'device_address_outside_policy'
              AND site_id IS NULL AND device_id IS NULL
        );

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('utility_accounts.view', 'Sites and devices', 'View utility accounts', 'View assigned-site utility accounts.', false);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'utility_accounts.view');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('utility_accounts.manage', 'Sites and devices', 'Manage utility accounts', 'Create, revise, and archive utility accounts.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'utility_accounts.manage');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('network.view', 'Sites and devices', 'View sensor network policy', 'View assigned-site network policy and observed addresses.', false);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'network.view');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('network.manage', 'Sites and devices', 'Manage sensor network policy', 'Change sensor network policies and CIDRs.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'network.manage');

INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        SELECT policy.id, CURRENT_TIMESTAMP, 'system', NULL,
               'network_policy.legacy_behavior_migrated', 'sensor_network_policy', policy.id,
               NULL, 'success', 'migration:20260721_0008',
               json_build_object('direction', policy.direction, 'mode', policy.mode,
                                 'behavior_preserved', true, 'review_required', true)
        FROM sensor_network_policies AS policy;

UPDATE alembic_version SET version_num='20260721_0008' WHERE alembic_version.version_num = '20260721_0007';

-- Running upgrade 20260721_0008 -> 20260723_0009

ALTER TABLE rate_versions ADD COLUMN pricing_model VARCHAR(32) DEFAULT 'time_of_use' NOT NULL;

CREATE INDEX ix_rate_versions_pricing_model ON rate_versions (pricing_model);

ALTER TABLE rate_versions ADD CONSTRAINT ck_rate_versions_rate_version_pricing_model CHECK (pricing_model IN ('flat','time_of_use','tiered','time_of_use_tiered'));

CREATE TABLE rate_tier_definitions (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    stable_tier_id VARCHAR(80) NOT NULL,
    name VARCHAR(120) NOT NULL,
    display_order INTEGER NOT NULL,
    lower_bound_kwh NUMERIC(20, 9) NOT NULL,
    upper_bound_kwh NUMERIC(20, 9),
    lower_bound_multiplier NUMERIC(16, 8),
    upper_bound_multiplier NUMERIC(16, 8),
    price_per_kwh NUMERIC(14, 8) NOT NULL,
    tou_prices JSON DEFAULT '{}'::json NOT NULL,
    season_name VARCHAR(80),
    source_citation VARCHAR(500),
    CONSTRAINT pk_rate_tier_definitions PRIMARY KEY (id),
    CONSTRAINT uq_rate_tier_stable_id UNIQUE (rate_version_id, stable_tier_id),
    CONSTRAINT uq_rate_tier_order UNIQUE (rate_version_id, display_order),
    CONSTRAINT ck_rate_tier_definitions_rate_tier_order_nonnegative CHECK (display_order >= 0),
    CONSTRAINT ck_rate_tier_definitions_rate_tier_lower_nonnegative CHECK (lower_bound_kwh >= 0),
    CONSTRAINT ck_rate_tier_definitions_rate_tier_bounds CHECK (upper_bound_kwh IS NULL OR upper_bound_kwh > lower_bound_kwh),
    CONSTRAINT ck_rate_tier_definitions_rate_tier_price_nonnegative CHECK (price_per_kwh >= 0),
    CONSTRAINT fk_rate_tier_definitions_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_tier_definitions_rate_version_id ON rate_tier_definitions (rate_version_id);

CREATE TABLE rate_threshold_rules (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    basis VARCHAR(32) DEFAULT 'fixed_cycle_kwh' NOT NULL,
    daily_baseline_kwh NUMERIC(18, 9),
    baseline_region VARCHAR(120),
    baseline_category VARCHAR(120),
    rounding_policy VARCHAR(32) DEFAULT 'none' NOT NULL,
    expected_cycle_start_day INTEGER DEFAULT '1' NOT NULL,
    source_citation VARCHAR(500),
    CONSTRAINT pk_rate_threshold_rules PRIMARY KEY (id),
    CONSTRAINT ck_rate_threshold_rules_rate_threshold_basis CHECK (basis IN ('fixed_cycle_kwh','daily_baseline_kwh')),
    CONSTRAINT ck_rate_threshold_rules_rate_threshold_rounding CHECK (rounding_policy IN ('none','nearest_kwh','floor_kwh','ceil_kwh')),
    CONSTRAINT ck_rate_threshold_rules_rate_threshold_cycle_day CHECK (expected_cycle_start_day >= 1 AND expected_cycle_start_day <= 31),
    CONSTRAINT uq_rate_threshold_rules_rate_version_id UNIQUE (rate_version_id),
    CONSTRAINT fk_rate_threshold_rules_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_threshold_rules_rate_version_id ON rate_threshold_rules (rate_version_id);

CREATE TABLE rate_seasonal_baselines (
    id VARCHAR(36) NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    name VARCHAR(80) NOT NULL,
    start_month INTEGER NOT NULL,
    start_day INTEGER NOT NULL,
    end_month INTEGER NOT NULL,
    end_day INTEGER NOT NULL,
    daily_kwh NUMERIC(18, 9) NOT NULL,
    source_citation VARCHAR(500),
    CONSTRAINT pk_rate_seasonal_baselines PRIMARY KEY (id),
    CONSTRAINT uq_rate_seasonal_baseline_name UNIQUE (rate_version_id, name),
    CONSTRAINT ck_rate_seasonal_baselines_rate_seasonal_baseline_positive CHECK (daily_kwh > 0),
    CONSTRAINT fk_rate_seasonal_baselines_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE CASCADE
);

CREATE INDEX ix_rate_seasonal_baselines_rate_version_id ON rate_seasonal_baselines (rate_version_id);

ALTER TABLE billing_cycles ADD COLUMN status VARCHAR(24) DEFAULT 'expected' NOT NULL;

ALTER TABLE billing_cycles ADD COLUMN boundary_source VARCHAR(32) DEFAULT 'generated' NOT NULL;

ALTER TABLE billing_cycles ADD COLUMN override_revision INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE billing_cycles ADD COLUMN recalculation_version INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE billing_cycles ADD COLUMN locked_snapshot_hash VARCHAR(64);

ALTER TABLE billing_cycles ADD COLUMN created_by VARCHAR(36);

ALTER TABLE billing_cycles ADD COLUMN updated_by VARCHAR(36);

ALTER TABLE billing_cycles ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE billing_cycles ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE billing_cycles ADD CONSTRAINT fk_billing_cycles_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE billing_cycles ADD CONSTRAINT fk_billing_cycles_updated_by_users FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL;

CREATE INDEX ix_billing_cycles_status ON billing_cycles (status);

ALTER TABLE billing_cycles ADD CONSTRAINT uq_billing_cycle_account_window UNIQUE (utility_account_id, starts_at, ends_at);

ALTER TABLE billing_cycles ADD CONSTRAINT ck_billing_cycles_billing_cycle_window CHECK (ends_at > starts_at);

ALTER TABLE billing_cycles ADD CONSTRAINT ck_billing_cycles_billing_cycle_status CHECK (status IN ('expected','confirmed','recalculating','finalized'));

ALTER TABLE billing_cycles ADD CONSTRAINT ck_billing_cycles_billing_cycle_boundary_source CHECK (boundary_source IN ('generated','manual_override','utility_import','external_feed'));

CREATE TABLE account_usage_authorities (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    authority_type VARCHAR(48) NOT NULL,
    aggregate_set_id VARCHAR(36),
    device_ids JSON DEFAULT '[]'::json NOT NULL,
    source_reference VARCHAR(500),
    confidence VARCHAR(24) DEFAULT 'unverified' NOT NULL,
    complete_account BOOLEAN DEFAULT false NOT NULL,
    revision INTEGER DEFAULT '1' NOT NULL,
    updated_by VARCHAR(36),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_account_usage_authorities PRIMARY KEY (id),
    CONSTRAINT ck_account_usage_authorities_account_usage_authority_type CHECK (authority_type IN ('complete_site_aggregate','service_leg_pair','whole_account_meter','utility_interval_import','manual_cycle_usage','external_feed','partial_monitored_circuits')),
    CONSTRAINT ck_account_usage_authorities_account_usage_authority_confidence CHECK (confidence IN ('unverified','low','medium','high','utility_verified')),
    CONSTRAINT uq_account_usage_authorities_utility_account_id UNIQUE (utility_account_id),
    CONSTRAINT fk_account_usage_authorities_utility_account_id_utility_42a1 FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE CASCADE,
    CONSTRAINT fk_account_usage_authorities_aggregate_set_id_aggregate_sets FOREIGN KEY(aggregate_set_id) REFERENCES aggregate_sets (id) ON DELETE SET NULL,
    CONSTRAINT fk_account_usage_authorities_updated_by_users FOREIGN KEY(updated_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_account_usage_authorities_utility_account_id ON account_usage_authorities (utility_account_id);

CREATE TABLE manual_account_usage (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    billing_cycle_id VARCHAR(36),
    effective_at TIMESTAMP WITH TIME ZONE NOT NULL,
    cumulative_kwh NUMERIC(24, 9) NOT NULL,
    source_note VARCHAR(500) NOT NULL,
    evidence_reference VARCHAR(500),
    idempotency_key VARCHAR(128) NOT NULL,
    verification_status VARCHAR(24) DEFAULT 'unverified' NOT NULL,
    superseded_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_manual_account_usage PRIMARY KEY (id),
    CONSTRAINT ck_manual_account_usage_manual_account_usage_nonnegative CHECK (cumulative_kwh >= 0),
    CONSTRAINT ck_manual_account_usage_manual_account_usage_verification CHECK (verification_status IN ('unverified','verified','reconciled')),
    CONSTRAINT uq_manual_usage_idempotency UNIQUE (utility_account_id, idempotency_key),
    CONSTRAINT fk_manual_account_usage_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_manual_account_usage_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_manual_account_usage_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_manual_account_usage_utility_account_id ON manual_account_usage (utility_account_id);

CREATE INDEX ix_manual_account_usage_billing_cycle_id ON manual_account_usage (billing_cycle_id);

CREATE INDEX ix_manual_account_usage_effective_at ON manual_account_usage (effective_at);

CREATE TABLE utility_usage_imports (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    import_kind VARCHAR(32) NOT NULL,
    status VARCHAR(24) DEFAULT 'preview' NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    source_name VARCHAR(240) NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    field_mapping JSON DEFAULT '{}'::json NOT NULL,
    row_count INTEGER DEFAULT '0' NOT NULL,
    conflict_count INTEGER DEFAULT '0' NOT NULL,
    normalized_rows JSON DEFAULT '[]'::json NOT NULL,
    reversed_at TIMESTAMP WITH TIME ZONE,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utility_usage_imports PRIMARY KEY (id),
    CONSTRAINT uq_utility_usage_import_content UNIQUE (utility_account_id, content_sha256),
    CONSTRAINT ck_utility_usage_imports_utility_usage_import_kind CHECK (import_kind IN ('interval','daily','cycle_cumulative','cycle_dates','bill_total')),
    CONSTRAINT ck_utility_usage_imports_utility_usage_import_status CHECK (status IN ('preview','committed','rejected','reversed')),
    CONSTRAINT fk_utility_usage_imports_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_usage_imports_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_utility_usage_imports_utility_account_id ON utility_usage_imports (utility_account_id);

CREATE INDEX ix_utility_usage_imports_content_sha256 ON utility_usage_imports (content_sha256);

CREATE TABLE tier_allocation_segments (
    id VARCHAR(36) NOT NULL,
    billing_cycle_id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    normalized_interval_id VARCHAR(36),
    import_id VARCHAR(36),
    segment_order INTEGER NOT NULL,
    interval_start TIMESTAMP WITH TIME ZONE NOT NULL,
    interval_end TIMESTAMP WITH TIME ZONE NOT NULL,
    rate_version_id VARCHAR(36) NOT NULL,
    tier_definition_id VARCHAR(36) NOT NULL,
    tier_stable_id VARCHAR(80) NOT NULL,
    tier_name VARCHAR(120) NOT NULL,
    tou_period VARCHAR(80),
    cumulative_start_kwh NUMERIC(24, 9) NOT NULL,
    cumulative_end_kwh NUMERIC(24, 9) NOT NULL,
    segment_energy_kwh NUMERIC(20, 9) NOT NULL,
    price_per_kwh NUMERIC(14, 8) NOT NULL,
    unrounded_energy_charge NUMERIC(24, 12) NOT NULL,
    derived_threshold_kwh NUMERIC(20, 9),
    usage_authority_type VARCHAR(48) NOT NULL,
    quality_flags JSON DEFAULT '[]'::json NOT NULL,
    recalculation_version INTEGER NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_tier_allocation_segments PRIMARY KEY (id),
    CONSTRAINT uq_tier_segment_interval_recalc UNIQUE (billing_cycle_id, normalized_interval_id, segment_order, recalculation_version),
    CONSTRAINT ck_tier_allocation_segments_tier_segment_energy_nonnegative CHECK (segment_energy_kwh >= 0),
    CONSTRAINT ck_tier_allocation_segments_tier_segment_cumulative_order CHECK (cumulative_end_kwh >= cumulative_start_kwh),
    CONSTRAINT fk_tier_allocation_segments_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE CASCADE,
    CONSTRAINT fk_tier_allocation_segments_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tier_allocation_segments_normalized_interval_id_norm_f88d FOREIGN KEY(normalized_interval_id) REFERENCES normalized_intervals (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tier_allocation_segments_import_id_utility_usage_imports FOREIGN KEY(import_id) REFERENCES utility_usage_imports (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tier_allocation_segments_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_tier_allocation_segments_tier_definition_id_rate_tie_0ee5 FOREIGN KEY(tier_definition_id) REFERENCES rate_tier_definitions (id) ON DELETE RESTRICT
);

CREATE INDEX ix_tier_allocation_segments_billing_cycle_id ON tier_allocation_segments (billing_cycle_id);

CREATE INDEX ix_tier_allocation_segments_utility_account_id ON tier_allocation_segments (utility_account_id);

CREATE INDEX ix_tier_allocation_segments_normalized_interval_id ON tier_allocation_segments (normalized_interval_id);

CREATE INDEX ix_tier_allocation_segments_interval_start ON tier_allocation_segments (interval_start);

CREATE INDEX ix_tier_allocation_segments_rate_version_id ON tier_allocation_segments (rate_version_id);

CREATE TABLE cycle_tier_summaries (
    billing_cycle_id VARCHAR(36) NOT NULL,
    tier_stable_id VARCHAR(80) NOT NULL,
    recalculation_version INTEGER NOT NULL,
    tier_name VARCHAR(120) NOT NULL,
    lower_bound_kwh NUMERIC(20, 9) NOT NULL,
    upper_bound_kwh NUMERIC(20, 9),
    usage_kwh NUMERIC(20, 9) NOT NULL,
    energy_charge NUMERIC(24, 12) NOT NULL,
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_cycle_tier_summaries PRIMARY KEY (billing_cycle_id, tier_stable_id, recalculation_version),
    CONSTRAINT fk_cycle_tier_summaries_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE CASCADE
);

CREATE TABLE tier_projection_snapshots (
    id VARCHAR(36) NOT NULL,
    billing_cycle_id VARCHAR(36) NOT NULL,
    calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    method VARCHAR(32) NOT NULL,
    projected_usage_kwh NUMERIC(20, 9) NOT NULL,
    projected_energy_charge NUMERIC(24, 12) NOT NULL,
    projected_tier_stable_id VARCHAR(80),
    confidence VARCHAR(24) NOT NULL,
    coverage_percent NUMERIC(7, 4) NOT NULL,
    CONSTRAINT pk_tier_projection_snapshots PRIMARY KEY (id),
    CONSTRAINT fk_tier_projection_snapshots_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE CASCADE
);

CREATE INDEX ix_tier_projection_snapshots_billing_cycle_id ON tier_projection_snapshots (billing_cycle_id);

CREATE INDEX ix_tier_projection_snapshots_calculated_at ON tier_projection_snapshots (calculated_at);

CREATE TABLE account_reconciliation_adjustments (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    billing_cycle_id VARCHAR(36) NOT NULL,
    component VARCHAR(48) NOT NULL,
    amount NUMERIC(18, 8) NOT NULL,
    notes VARCHAR(1000) NOT NULL,
    provenance VARCHAR(500) NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_account_reconciliation_adjustments PRIMARY KEY (id),
    CONSTRAINT fk_account_reconciliation_adjustments_utility_account_i_0391 FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_account_reconciliation_adjustments_billing_cycle_id__38c1 FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_account_reconciliation_adjustments_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_account_reconciliation_adjustments_utility_account_id ON account_reconciliation_adjustments (utility_account_id);

CREATE INDEX ix_account_reconciliation_adjustments_billing_cycle_id ON account_reconciliation_adjustments (billing_cycle_id);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('costs.recalculate', 'Rates and billing', 'Recalculate costs', 'Recalculate unfinalized billing-cycle cost allocations.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'costs.recalculate');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('usage_imports.manage', 'Rates and billing', 'Manage utility usage imports', 'Preview, commit, reconcile, and reverse utility usage imports.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'usage_imports.manage');

UPDATE alembic_version SET version_num='20260723_0009' WHERE alembic_version.version_num = '20260721_0008';

-- Running upgrade 20260723_0009 -> 20260724_0010

CREATE TABLE utility_bill_imports (
    id VARCHAR(36) NOT NULL,
    job_id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    artifact_id VARCHAR(36) NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    status VARCHAR(32) DEFAULT 'review_required' NOT NULL,
    source_role VARCHAR(40) DEFAULT 'supporting' NOT NULL,
    extraction_method VARCHAR(16) NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    page_count INTEGER NOT NULL,
    retention_mode VARCHAR(32) DEFAULT 'retain' NOT NULL,
    retain_until TIMESTAMP WITH TIME ZONE,
    original_deleted_at TIMESTAMP WITH TIME ZONE,
    sanitized_evidence_path VARCHAR(1000) NOT NULL,
    rate_plan_id VARCHAR(36),
    rate_version_id VARCHAR(36),
    revision INTEGER DEFAULT '1' NOT NULL,
    blocking_warnings JSON DEFAULT '[]'::json NOT NULL,
    extraction_warnings JSON DEFAULT '[]'::json NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    reviewed_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_utility_bill_imports PRIMARY KEY (id),
    CONSTRAINT uq_utility_bill_import_account_hash UNIQUE (utility_account_id, content_sha256),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_status CHECK (status IN ('processing','review_required','ready_to_publish','published','rejected','failed')),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_source_role CHECK (source_role IN ('supporting','authoritative_account_specific','reference_only')),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_extraction_method CHECK (extraction_method IN ('text','ocr','mixed')),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_retention CHECK (retention_mode IN ('retain','retain_until','delete_after_approval')),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_page_count CHECK (page_count > 0),
    CONSTRAINT ck_utility_bill_imports_utility_bill_import_revision CHECK (revision > 0),
    CONSTRAINT uq_utility_bill_imports_job_id UNIQUE (job_id),
    CONSTRAINT fk_utility_bill_imports_job_id_background_jobs FOREIGN KEY(job_id) REFERENCES background_jobs (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_bill_imports_utility_account_id_utility_accounts FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT uq_utility_bill_imports_artifact_id UNIQUE (artifact_id),
    CONSTRAINT fk_utility_bill_imports_artifact_id_rate_source_artifacts FOREIGN KEY(artifact_id) REFERENCES rate_source_artifacts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_bill_imports_rate_plan_id_rate_plans FOREIGN KEY(rate_plan_id) REFERENCES rate_plans (id) ON DELETE SET NULL,
    CONSTRAINT fk_utility_bill_imports_rate_version_id_rate_versions FOREIGN KEY(rate_version_id) REFERENCES rate_versions (id) ON DELETE SET NULL,
    CONSTRAINT fk_utility_bill_imports_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_bill_imports_reviewed_by_users FOREIGN KEY(reviewed_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_bill_imports_job_id ON utility_bill_imports (job_id);

CREATE INDEX ix_utility_bill_imports_utility_account_id ON utility_bill_imports (utility_account_id);

CREATE INDEX ix_utility_bill_imports_artifact_id ON utility_bill_imports (artifact_id);

CREATE INDEX ix_utility_bill_imports_content_sha256 ON utility_bill_imports (content_sha256);

CREATE INDEX ix_utility_bill_imports_status ON utility_bill_imports (status);

CREATE INDEX ix_utility_bill_imports_rate_plan_id ON utility_bill_imports (rate_plan_id);

CREATE INDEX ix_utility_bill_imports_rate_version_id ON utility_bill_imports (rate_version_id);

CREATE INDEX ix_utility_bill_imports_created_at ON utility_bill_imports (created_at);

CREATE TABLE utility_bill_extraction_revisions (
    id VARCHAR(36) NOT NULL,
    bill_import_id VARCHAR(36) NOT NULL,
    revision INTEGER NOT NULL,
    status VARCHAR(24) DEFAULT 'review_required' NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    ocr_version VARCHAR(80),
    normalized_account_data JSON DEFAULT '{}'::json NOT NULL,
    normalized_rate_data JSON DEFAULT '{}'::json NOT NULL,
    normalized_cycle_data JSON DEFAULT '{}'::json NOT NULL,
    raw_text_sha256 VARCHAR(64) NOT NULL,
    normalized_text_sha256 VARCHAR(64) NOT NULL,
    sanitized_text_path VARCHAR(1000) NOT NULL,
    extraction_metadata JSON DEFAULT '{}'::json NOT NULL,
    created_by VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utility_bill_extraction_revisions PRIMARY KEY (id),
    CONSTRAINT uq_utility_bill_extraction_revision UNIQUE (bill_import_id, revision),
    CONSTRAINT ck_utility_bill_extraction_revisions_utility_bill_extra_9ea0 CHECK (status IN ('review_required','approved','superseded','failed')),
    CONSTRAINT ck_utility_bill_extraction_revisions_utility_bill_extra_c326 CHECK (revision > 0),
    CONSTRAINT fk_utility_bill_extraction_revisions_bill_import_id_uti_55e3 FOREIGN KEY(bill_import_id) REFERENCES utility_bill_imports (id) ON DELETE CASCADE,
    CONSTRAINT fk_utility_bill_extraction_revisions_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_utility_bill_extraction_revisions_bill_import_id ON utility_bill_extraction_revisions (bill_import_id);

CREATE INDEX ix_utility_bill_extraction_revisions_created_at ON utility_bill_extraction_revisions (created_at);

CREATE TABLE utility_bill_extracted_fields (
    id VARCHAR(36) NOT NULL,
    extraction_revision_id VARCHAR(36) NOT NULL,
    output_kind VARCHAR(24) NOT NULL,
    field_key VARCHAR(240) NOT NULL,
    raw_value JSON,
    normalized_value JSON,
    corrected_value JSON,
    page_number INTEGER,
    text_region JSON,
    source_excerpt TEXT,
    extraction_method VARCHAR(16) NOT NULL,
    parser_version VARCHAR(40) NOT NULL,
    confidence VARCHAR(32) NOT NULL,
    review_state VARCHAR(24) DEFAULT 'unreviewed' NOT NULL,
    warnings JSON DEFAULT '[]'::json NOT NULL,
    normalization_history JSON DEFAULT '[]'::json NOT NULL,
    confirmed_by VARCHAR(36),
    confirmed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_utility_bill_extracted_fields PRIMARY KEY (id),
    CONSTRAINT uq_utility_bill_extracted_field UNIQUE (extraction_revision_id, output_kind, field_key),
    CONSTRAINT ck_utility_bill_extracted_fields_utility_bill_field_output_kind CHECK (output_kind IN ('account','rate_plan','billing_cycle')),
    CONSTRAINT ck_utility_bill_extracted_fields_utility_bill_field_method CHECK (extraction_method IN ('text','ocr','mixed','administrator')),
    CONSTRAINT ck_utility_bill_extracted_fields_utility_bill_field_confidence CHECK (confidence IN ('administrator_confirmed','high','medium','low','missing','conflicts_current','conflicts_source','not_applicable')),
    CONSTRAINT ck_utility_bill_extracted_fields_utility_bill_field_rev_ec48 CHECK (review_state IN ('unreviewed','confirmed','corrected','rejected')),
    CONSTRAINT fk_utility_bill_extracted_fields_extraction_revision_id_2a1b FOREIGN KEY(extraction_revision_id) REFERENCES utility_bill_extraction_revisions (id) ON DELETE CASCADE,
    CONSTRAINT fk_utility_bill_extracted_fields_confirmed_by_users FOREIGN KEY(confirmed_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_bill_extracted_fields_extraction_revision_id ON utility_bill_extracted_fields (extraction_revision_id);

CREATE INDEX ix_utility_bill_extracted_fields_output_kind ON utility_bill_extracted_fields (output_kind);

CREATE INDEX ix_utility_bill_extracted_fields_confidence ON utility_bill_extracted_fields (confidence);

CREATE TABLE utility_bill_field_conflicts (
    id VARCHAR(36) NOT NULL,
    bill_import_id VARCHAR(36) NOT NULL,
    field_key VARCHAR(240) NOT NULL,
    extracted_value JSON,
    configured_value JSON,
    comparison_source VARCHAR(120) NOT NULL,
    status VARCHAR(24) DEFAULT 'unresolved' NOT NULL,
    blocking BOOLEAN DEFAULT true NOT NULL,
    resolution_note VARCHAR(1000),
    resolved_by VARCHAR(36),
    resolved_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_utility_bill_field_conflicts PRIMARY KEY (id),
    CONSTRAINT uq_utility_bill_field_conflict UNIQUE (bill_import_id, field_key, comparison_source),
    CONSTRAINT ck_utility_bill_field_conflicts_utility_bill_conflict_status CHECK (status IN ('unresolved','accepted_bill','accepted_configured','dismissed')),
    CONSTRAINT fk_utility_bill_field_conflicts_bill_import_id_utility__020a FOREIGN KEY(bill_import_id) REFERENCES utility_bill_imports (id) ON DELETE CASCADE,
    CONSTRAINT fk_utility_bill_field_conflicts_resolved_by_users FOREIGN KEY(resolved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_bill_field_conflicts_bill_import_id ON utility_bill_field_conflicts (bill_import_id);

CREATE INDEX ix_utility_bill_field_conflicts_status ON utility_bill_field_conflicts (status);

CREATE TABLE utility_bill_cycle_drafts (
    id VARCHAR(36) NOT NULL,
    bill_import_id VARCHAR(36) NOT NULL,
    extraction_revision_id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    status VARCHAR(24) DEFAULT 'draft' NOT NULL,
    starts_at TIMESTAMP WITH TIME ZONE,
    ends_at TIMESTAMP WITH TIME ZONE,
    cycle_days INTEGER,
    meter_read_date DATE,
    total_usage_kwh NUMERIC(24, 9),
    usage_by_tier JSON DEFAULT '[]'::json NOT NULL,
    usage_by_tou JSON DEFAULT '[]'::json NOT NULL,
    meter_records JSON DEFAULT '[]'::json NOT NULL,
    current_tier VARCHAR(120),
    projected_tier VARCHAR(120),
    energy_subtotal NUMERIC(24, 12),
    full_bill_total NUMERIC(24, 12),
    fixed_charges NUMERIC(24, 12),
    taxes_fees NUMERIC(24, 12),
    credits NUMERIC(24, 12),
    adjustments NUMERIC(24, 12),
    threshold_interpretation VARCHAR(40) DEFAULT 'unknown' NOT NULL,
    reconciliation_status VARCHAR(32) DEFAULT 'not_compared' NOT NULL,
    billing_cycle_id VARCHAR(36),
    utility_usage_import_id VARCHAR(36),
    revision INTEGER DEFAULT '1' NOT NULL,
    reviewed_by VARCHAR(36),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
    approved_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT pk_utility_bill_cycle_drafts PRIMARY KEY (id),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_draft_status CHECK (status IN ('draft','approved','imported','rejected')),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_thresho_21e6 CHECK (threshold_interpretation IN ('fixed_cycle_threshold','daily_baseline','baseline_multiplier','unknown')),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_reconciliation CHECK (reconciliation_status IN ('not_compared','matched','difference','adjusted')),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_window CHECK (starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_usage_n_efc5 CHECK (total_usage_kwh IS NULL OR total_usage_kwh >= 0),
    CONSTRAINT ck_utility_bill_cycle_drafts_utility_bill_cycle_revision CHECK (revision > 0),
    CONSTRAINT uq_utility_bill_cycle_drafts_bill_import_id UNIQUE (bill_import_id),
    CONSTRAINT fk_utility_bill_cycle_drafts_bill_import_id_utility_bil_96a4 FOREIGN KEY(bill_import_id) REFERENCES utility_bill_imports (id) ON DELETE CASCADE,
    CONSTRAINT fk_utility_bill_cycle_drafts_extraction_revision_id_uti_43a4 FOREIGN KEY(extraction_revision_id) REFERENCES utility_bill_extraction_revisions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_bill_cycle_drafts_utility_account_id_utility_2977 FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_bill_cycle_drafts_billing_cycle_id_billing_cycles FOREIGN KEY(billing_cycle_id) REFERENCES billing_cycles (id) ON DELETE SET NULL,
    CONSTRAINT fk_utility_bill_cycle_drafts_utility_usage_import_id_ut_b2ba FOREIGN KEY(utility_usage_import_id) REFERENCES utility_usage_imports (id) ON DELETE SET NULL,
    CONSTRAINT fk_utility_bill_cycle_drafts_reviewed_by_users FOREIGN KEY(reviewed_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_bill_cycle_drafts_bill_import_id ON utility_bill_cycle_drafts (bill_import_id);

CREATE INDEX ix_utility_bill_cycle_drafts_extraction_revision_id ON utility_bill_cycle_drafts (extraction_revision_id);

CREATE INDEX ix_utility_bill_cycle_drafts_utility_account_id ON utility_bill_cycle_drafts (utility_account_id);

CREATE INDEX ix_utility_bill_cycle_drafts_status ON utility_bill_cycle_drafts (status);

CREATE INDEX ix_utility_bill_cycle_drafts_billing_cycle_id ON utility_bill_cycle_drafts (billing_cycle_id);

CREATE INDEX ix_utility_bill_cycle_drafts_usage_import_id ON utility_bill_cycle_drafts (utility_usage_import_id);

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('utility_bills.view', 'Rates and billing', 'View utility bill imports', 'View private utility-bill extraction evidence and comparison history.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'utility_bills.view');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('utility_bills.manage', 'Rates and billing', 'Manage utility bill imports', 'Upload, review, publish, retain, and delete private utility-bill artifacts.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'utility_bills.manage');

UPDATE alembic_version SET version_num='20260724_0010' WHERE alembic_version.version_num = '20260723_0009';

-- Running upgrade 20260724_0010 -> 20260724_0011

ALTER TABLE users ADD COLUMN lifecycle_state VARCHAR(16) DEFAULT 'active' NOT NULL;

ALTER TABLE users ADD COLUMN is_protected BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE users ADD COLUMN removed_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD COLUMN removed_by VARCHAR(36);

ALTER TABLE users ADD CONSTRAINT fk_users_removed_by_users FOREIGN KEY(removed_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE users ADD COLUMN removal_reason VARCHAR(500);

ALTER TABLE users ADD COLUMN restored_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE users ADD COLUMN restored_by VARCHAR(36);

ALTER TABLE users ADD CONSTRAINT fk_users_restored_by_users FOREIGN KEY(restored_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE users ADD COLUMN removed_role_ids JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE users ADD COLUMN removed_site_ids JSON DEFAULT '[]'::json NOT NULL;

ALTER TABLE users ADD COLUMN removed_all_sites BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE users ADD CONSTRAINT ck_users_user_lifecycle_state CHECK (lifecycle_state IN ('active','disabled','removed'));

CREATE INDEX ix_users_lifecycle_state ON users (lifecycle_state);

CREATE INDEX ix_users_removed_at ON users (removed_at);

UPDATE users SET lifecycle_state = CASE WHEN is_active THEN 'active' ELSE 'disabled' END;

UPDATE users
        SET is_protected = true
        WHERE id = (
            SELECT users.id
            FROM users
            JOIN user_roles ON user_roles.user_id = users.id
            WHERE user_roles.role_name = 'admin'
            ORDER BY users.created_at, users.id
            LIMIT 1
        );

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.disable', 'Administration', 'Disable and enable users', 'Temporarily suspend and re-enable local user accounts.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.disable');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.remove', 'Administration', 'Remove users', 'Safely deprovision local users while preserving historical identity records.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.remove');

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('users.restore', 'Administration', 'Restore removed users', 'Restore removed identities to a disabled, unassigned state for explicit review.', true);

INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', 'users.restore');

UPDATE alembic_version SET version_num='20260724_0011' WHERE alembic_version.version_num = '20260724_0010';

-- Running upgrade 20260724_0011 -> 20260724_0012

ALTER TABLE sites ADD COLUMN code VARCHAR(80);

ALTER TABLE sites ADD COLUMN description TEXT;

ALTER TABLE sites ADD COLUMN location_label VARCHAR(160);

ALTER TABLE sites ADD COLUMN organization VARCHAR(160);

ALTER TABLE sites ADD COLUMN currency VARCHAR(3) DEFAULT 'USD' NOT NULL;

ALTER TABLE sites ADD COLUMN locale VARCHAR(32) DEFAULT 'en-US' NOT NULL;

ALTER TABLE sites ADD COLUMN unit_system VARCHAR(16) DEFAULT 'imperial' NOT NULL;

ALTER TABLE sites ADD COLUMN lifecycle_state VARCHAR(16) DEFAULT 'active' NOT NULL;

ALTER TABLE sites ADD COLUMN is_default BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE sites ADD COLUMN disabled_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE sites ADD COLUMN disabled_by VARCHAR(36);

ALTER TABLE sites ADD CONSTRAINT fk_sites_disabled_by_users FOREIGN KEY(disabled_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE sites ADD COLUMN removed_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE sites ADD COLUMN removed_by VARCHAR(36);

ALTER TABLE sites ADD CONSTRAINT fk_sites_removed_by_users FOREIGN KEY(removed_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE sites ADD COLUMN removal_reason VARCHAR(500);

ALTER TABLE sites ADD COLUMN restored_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE sites ADD COLUMN restored_by VARCHAR(36);

ALTER TABLE sites ADD CONSTRAINT fk_sites_restored_by_users FOREIGN KEY(restored_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE sites ADD COLUMN revision INTEGER DEFAULT '1' NOT NULL;

UPDATE sites
        SET code =
            COALESCE(
                NULLIF(
                    trim(BOTH '-' FROM regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')),
                    ''
                ),
                'site'
            ) || '-' || substring(replace(id, '-', '') FROM 1 FOR 8);

ALTER TABLE sites ALTER COLUMN code SET NOT NULL;

UPDATE sites
        SET is_default = true
        WHERE id = (
            SELECT id FROM sites
            WHERE lifecycle_state = 'active'
            ORDER BY created_at, id
            LIMIT 1
        );

ALTER TABLE sites ADD CONSTRAINT ck_sites_site_lifecycle_state CHECK (lifecycle_state IN ('active','disabled','removed'));

ALTER TABLE sites ADD CONSTRAINT ck_sites_site_currency CHECK (length(currency) = 3);

ALTER TABLE sites ADD CONSTRAINT ck_sites_site_unit_system CHECK (unit_system IN ('imperial','metric'));

ALTER TABLE sites ADD CONSTRAINT ck_sites_site_revision_positive CHECK (revision > 0);

CREATE UNIQUE INDEX ix_sites_code ON sites (code);

CREATE INDEX ix_sites_lifecycle_state ON sites (lifecycle_state);

CREATE INDEX ix_sites_is_default ON sites (is_default);

CREATE INDEX ix_sites_disabled_at ON sites (disabled_at);

CREATE INDEX ix_sites_removed_at ON sites (removed_at);

CREATE INDEX ix_sites_disabled_by ON sites (disabled_by);

CREATE INDEX ix_sites_removed_by ON sites (removed_by);

CREATE UNIQUE INDEX uq_sites_single_active_default ON sites (is_default) WHERE is_default = true AND lifecycle_state = 'active';

CREATE TABLE device_site_assignments (
    id VARCHAR(36) NOT NULL,
    device_id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to TIMESTAMP WITH TIME ZONE,
    assigned_by VARCHAR(36),
    reason VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_device_site_assignments PRIMARY KEY (id),
    CONSTRAINT ck_device_site_assignments_device_site_assignment_window CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT fk_device_site_assignments_device_id_devices FOREIGN KEY(device_id) REFERENCES devices (id) ON DELETE RESTRICT,
    CONSTRAINT fk_device_site_assignments_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_device_site_assignments_assigned_by_users FOREIGN KEY(assigned_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_device_site_assignments_device_id ON device_site_assignments (device_id);

CREATE INDEX ix_device_site_assignments_site_id ON device_site_assignments (site_id);

CREATE INDEX ix_device_site_assignments_effective_from ON device_site_assignments (effective_from);

CREATE INDEX ix_device_site_assignments_effective_to ON device_site_assignments (effective_to);

CREATE UNIQUE INDEX uq_device_site_assignment_open ON device_site_assignments (device_id) WHERE effective_to IS NULL;

INSERT INTO device_site_assignments
            (id, device_id, site_id, effective_from, effective_to,
             assigned_by, reason, created_at)
        SELECT
            id,
            id,
            site_id,
            created_at,
            NULL,
            NULL,
            'System migration: existing device site',
            CURRENT_TIMESTAMP
        FROM devices;

CREATE TABLE utility_account_site_assignments (
    id VARCHAR(36) NOT NULL,
    utility_account_id VARCHAR(36) NOT NULL,
    site_id VARCHAR(36) NOT NULL,
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL,
    effective_to TIMESTAMP WITH TIME ZONE,
    assigned_by VARCHAR(36),
    reason VARCHAR(500),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    CONSTRAINT pk_utility_account_site_assignments PRIMARY KEY (id),
    CONSTRAINT ck_utility_account_site_assignments_utility_account_sit_dfef CHECK (effective_to IS NULL OR effective_to > effective_from),
    CONSTRAINT fk_utility_account_site_assignments_utility_account_id__cf34 FOREIGN KEY(utility_account_id) REFERENCES utility_accounts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_account_site_assignments_site_id_sites FOREIGN KEY(site_id) REFERENCES sites (id) ON DELETE RESTRICT,
    CONSTRAINT fk_utility_account_site_assignments_assigned_by_users FOREIGN KEY(assigned_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_utility_account_site_assignments_utility_account_id ON utility_account_site_assignments (utility_account_id);

CREATE INDEX ix_utility_account_site_assignments_site_id ON utility_account_site_assignments (site_id);

CREATE INDEX ix_utility_account_site_assignments_effective_from ON utility_account_site_assignments (effective_from);

CREATE INDEX ix_utility_account_site_assignments_effective_to ON utility_account_site_assignments (effective_to);

CREATE UNIQUE INDEX uq_utility_account_site_assignment_open ON utility_account_site_assignments (utility_account_id) WHERE effective_to IS NULL;

INSERT INTO utility_account_site_assignments
            (id, utility_account_id, site_id, effective_from, effective_to,
             assigned_by, reason, created_at)
        SELECT
            id,
            id,
            site_id,
            created_at,
            NULL,
            NULL,
            'System migration: existing utility-account site',
            CURRENT_TIMESTAMP
        FROM utility_accounts;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.create', 'Sites and devices', 'Create sites', 'Create a physical site and its initial network-policy boundary.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.create'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.create')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.edit', 'Sites and devices', 'Edit sites', 'Change assigned-site identity, locale, timezone, and policy assignment.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.edit'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.edit')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.set_default', 'Sites and devices', 'Set default site', 'Transactionally change the active default site.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.set_default'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.set_default')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.disable', 'Sites and devices', 'Disable and enable sites', 'Temporarily suspend ordinary access and new assignments for a site.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.disable'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.disable')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.remove', 'Sites and devices', 'Remove sites', 'Soft-remove a site after reviewing and resolving active dependencies.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.remove'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.remove')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.restore', 'Sites and devices', 'Restore sites', 'Restore a removed site to a disabled state for explicit review.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.restore'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.restore')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.transfer_resources', 'Sites and devices', 'Transfer site resources', 'Transfer or archive active site resources before removal.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.transfer_resources'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.transfer_resources')
                ON CONFLICT DO NOTHING;

INSERT INTO permissions (code, group_name, label, description, high_risk) VALUES ('sites.view_audit', 'Sites and devices', 'View site audit history', 'View lifecycle and configuration audit evidence for assigned sites.', true);

INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, 'sites.view_audit'
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING;

INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', 'sites.view_audit')
                ON CONFLICT DO NOTHING;

INSERT INTO status_layout_revisions
            (id, revision, registry_version, configuration, created_by, created_at,
             reason, restored_from_id)
        SELECT
            '00000000-0000-4000-8000-000000000012',
            state.current_revision + 1,
            current.registry_version,
            jsonb_set(
                current.configuration::jsonb,
                '{items}',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            item || jsonb_build_object(
                                'zone',
                                CASE item->>'zone'
                                    WHEN 'global_header_left' THEN 'top_bar'
                                    WHEN 'global_header_center' THEN 'top_bar'
                                    WHEN 'global_header_right' THEN 'top_bar'
                                    WHEN 'sidebar_upper' THEN 'mobile_status_drawer'
                                    WHEN 'sidebar_lower' THEN 'mobile_status_drawer'
                                    WHEN 'global_footer' THEN 'page_summary'
                                    WHEN 'page_header_primary' THEN 'workspace_header'
                                    WHEN 'page_header_secondary' THEN 'workspace_header'
                                    WHEN 'page_status_row' THEN 'page_summary'
                                    WHEN 'page_summary_strip' THEN 'page_summary'
                                    WHEN 'page_footer' THEN 'page_summary'
                                    WHEN 'overview_site_state' THEN 'overview_summary'
                                    WHEN 'overview_site_summary' THEN 'overview_summary'
                                    WHEN 'history_context' THEN 'page_summary'
                                    WHEN 'diagnostics_summary'
                                        THEN 'administration_diagnostics'
                                    WHEN 'mobile_header' THEN 'mobile_status_drawer'
                                    WHEN 'mobile_status_strip' THEN 'mobile_status_drawer'
                                    ELSE item->>'zone'
                                END
                            )
                        )
                        FROM jsonb_array_elements(
                            COALESCE(current.configuration::jsonb->'items', '[]'::jsonb)
                        ) AS item
                    ),
                    '[]'::jsonb
                ),
                true
            )::json,
            NULL,
            CURRENT_TIMESTAMP,
            'System migration: six-workspace shell and semantic status zones',
            state.current_revision_id
        FROM status_layout_state AS state
        JOIN status_layout_revisions AS current ON current.id = state.current_revision_id
        WHERE state.id = 'current';

UPDATE status_layout_state
        SET current_revision_id = '00000000-0000-4000-8000-000000000012',
            current_revision = current_revision + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 'current'
          AND EXISTS (
              SELECT 1 FROM status_layout_revisions
              WHERE id = '00000000-0000-4000-8000-000000000012'
          );

INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        VALUES
            ('00000000-0000-4000-9000-000000000012', CURRENT_TIMESTAMP, 'system', NULL,
             'site_lifecycle.modern_workspace_migrated', 'status_layout',
             '00000000-0000-4000-8000-000000000012', NULL, 'success',
             'migration:20260724_0012',
             json_build_object(
                 'summary', 'Added site lifecycle and six-workspace semantic zones',
                 'previous_revision_preserved', true,
                 'existing_sites_preserved', true,
                 'raw_readings_rewritten', false
             ));

UPDATE alembic_version SET version_num='20260724_0012' WHERE alembic_version.version_num = '20260724_0011';

-- Running upgrade 20260724_0012 -> 20260724_0013

ALTER TABLE utility_bill_imports ALTER COLUMN utility_account_id DROP NOT NULL;

ALTER TABLE utility_bill_cycle_drafts ALTER COLUMN utility_account_id DROP NOT NULL;

CREATE UNIQUE INDEX uq_utility_bill_import_unassigned_creator_hash ON utility_bill_imports (created_by, content_sha256) WHERE utility_account_id IS NULL;

UPDATE alembic_version SET version_num='20260724_0013' WHERE alembic_version.version_num = '20260724_0012';

COMMIT;

