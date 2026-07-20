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

COMMIT;

