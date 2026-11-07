-- 1-second readings, kept ~24 h. received_at = when the cloud got it (drives "last seen").
create table if not exists readings (
  drive_id text not null, ts timestamptz not null,
  received_at timestamptz not null default now(),
  simulated boolean not null default true,
  state text, status text,
  freq real, current real, power real, speed real, torque real, dc_link real,
  heatsink real, motor_temp real, vib_rms real, vib_peak real,
  primary key (drive_id, ts)                      -- same reading sent twice = ignored
);
-- 1-minute roll-ups, kept long term (see retention in app.py)
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
-- per-drive "normal" learned from early run-state data (for z-scores)
create table if not exists baselines (
  drive_id text not null, signal text not null, mean real, std real, n int,
  primary key (drive_id, signal)
);
-- Supabase: block its public REST API from these tables (our backend connects as owner and bypasses RLS)
alter table readings enable row level security;
alter table readings_1m enable row level security;
alter table events enable row level security;
alter table baselines enable row level security;
