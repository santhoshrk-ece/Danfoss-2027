-- 1-second readings, kept ~24 h. received_at = when the cloud got it (drives "last seen" and latency).
create table if not exists readings (
  drive_id text not null, ts timestamptz not null,
  received_at timestamptz not null default now(),
  simulated boolean not null default true,
  state text, status text,
  freq real, current real, power real, speed real, torque real, dc_link real,
  heatsink real, motor_temp real, vib_rms real, vib_peak real,
  primary key (drive_id, ts)
);
create table if not exists readings_1m (
  drive_id text not null, ts timestamptz not null, n int,
  current real, current_max real, power real, heatsink real, motor_temp real,
  vib_rms real, vib_rms_max real,
  primary key (drive_id, ts)
);
create table if not exists events (
  id bigserial primary key, drive_id text not null, ts timestamptz not null,
  severity text, signal text, value real, msg text,
  unique (drive_id, ts, signal, severity)
);
alter table events add column if not exists received_at timestamptz not null default now();
-- drive registry: each drive has its own rated values and optional limit overrides
create table if not exists drives (
  drive_id text primary key, name text, model text,
  rated_kw real not null default 4.0, rated_a real not null default 10.0,
  limits jsonb not null default '{}'::jsonb,
  commissioned_at timestamptz, created_at timestamptz not null default now()
);
-- latest edge health report per drive (buffer depth, failures, uptime...)
create table if not exists drive_status (
  drive_id text primary key, updated_at timestamptz not null default now(),
  boot_id text, uptime_s real, buffer_depth int, failures int, last_upload_ms real
);
-- learned "normal" per drive (see anomaly.py)
create table if not exists anomaly_models (
  drive_id text primary key, trained_at timestamptz not null default now(), model jsonb not null
);
alter table readings enable row level security;
alter table readings_1m enable row level security;
alter table events enable row level security;
alter table drives enable row level security;
alter table drive_status enable row level security;
alter table anomaly_models enable row level security;
