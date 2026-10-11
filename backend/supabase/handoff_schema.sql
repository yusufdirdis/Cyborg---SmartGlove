-- handoff persistence inside the shared Resume Fit Supabase project.
-- Everything lives in its own `handoff` schema: Resume Fit's `public` schema, auth
-- settings and policies are not touched. The schema is not exposed through the Data
-- API and browser roles get no grants; only the FastAPI backend connects, as handoff_app.
-- Safe to re-run. Apply once as an admin (SQL editor or MCP apply_migration),
-- never with `supabase db push` against this project.

create schema if not exists handoff;

create table if not exists handoff.macros (
  id          uuid primary key default gen_random_uuid(),
  name        text not null check (char_length(name) between 1 and 60),
  description text not null default '' check (char_length(description) <= 240),
  kind        text not null check (kind in ('pose', 'sequence')),
  steps       jsonb not null check (jsonb_typeof(steps) = 'array' and jsonb_array_length(steps) between 1 and 30),
  revision    integer not null default 1 check (revision > 0),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check (kind = 'sequence' or jsonb_array_length(steps) = 1)
);

create table if not exists handoff.calibration (
  servo_id   smallint primary key check (servo_id between 1 and 6),
  data       jsonb not null check (jsonb_typeof(data) = 'object'),
  revision   integer not null default 1 check (revision > 0),
  updated_at timestamptz not null default now()
);

create table if not exists handoff.device_sessions (
  id         uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  ended_at   timestamptz,
  port       text,
  api_pid    integer,
  readings   jsonb,
  instance   text not null default 'local'
);

create table if not exists handoff.estop_checks (
  id               uuid primary key default gen_random_uuid(),
  session_id       uuid references handoff.device_sessions (id) on delete set null,
  checked_at       timestamptz not null default now(),
  off_body         boolean not null,
  power_cut_tested boolean not null,
  cord_path_clear  boolean not null,
  context          text not null check (context in ('jog', 'position', 'group')),
  instance         text not null default 'local'
);

create table if not exists handoff.hardware_moves (
  id         uuid primary key default gen_random_uuid(),
  session_id uuid references handoff.device_sessions (id) on delete set null,
  at         timestamptz not null default now(),
  kind       text not null check (kind in ('jog', 'position', 'group', 'live', 'torque_enable')),
  servo_ids  jsonb not null check (jsonb_typeof(servo_ids) = 'array'),
  request    jsonb,
  ok         boolean not null,
  result     jsonb,
  error      text,
  instance   text not null default 'local'
);

create table if not exists handoff.ai_proposals (
  id          text primary key,
  provider    text not null,
  command     jsonb not null,
  status      text not null check (status in ('proposed', 'confirmed', 'saved', 'cancelled', 'expired')),
  created_at  timestamptz not null default now(),
  resolved_at timestamptz,
  instance    text not null default 'local'
);

create table if not exists handoff.vision_detections (
  id          uuid primary key default gen_random_uuid(),
  received_at timestamptz not null default now(),
  label       text not null check (char_length(label) between 1 and 80),
  confidence  real not null check (confidence between 0 and 1),
  source      text not null check (source in ('demo', 'external')),
  instance    text not null default 'local'
);

-- Data format version, upgraded by the API (store.py) inside one locked transaction.
-- 1 = finger IDs remapped (every row comes from SQLite at version 1 or later); 2 = raw 0-4000 step counts.
create table if not exists handoff.meta (
  key   text primary key,
  value text not null
);
insert into handoff.meta (key, value) values ('data_version', '1') on conflict (key) do nothing;

-- Physical macro playback (main: "Use raw synchronized targets for macros") is logged too.
alter table handoff.hardware_moves drop constraint if exists hardware_moves_kind_check;
alter table handoff.hardware_moves add constraint hardware_moves_kind_check
  check (kind in ('jog', 'position', 'group', 'live', 'torque_enable', 'macro'));
alter table handoff.estop_checks drop constraint if exists estop_checks_context_check;
alter table handoff.estop_checks add constraint estop_checks_context_check
  check (context in ('jog', 'position', 'group', 'macro'));

create index if not exists device_sessions_started_at on handoff.device_sessions (started_at desc);
create index if not exists estop_checks_checked_at on handoff.estop_checks (checked_at desc);
create index if not exists hardware_moves_at on handoff.hardware_moves (at desc);
create index if not exists hardware_moves_session on handoff.hardware_moves (session_id);
create index if not exists estop_checks_session on handoff.estop_checks (session_id);
create index if not exists ai_proposals_created_at on handoff.ai_proposals (created_at desc);
create index if not exists vision_detections_received_at on handoff.vision_detections (received_at desc);

-- Defense in depth: RLS on, no policies, no browser-role access.
alter table handoff.macros            enable row level security;
alter table handoff.calibration       enable row level security;
alter table handoff.device_sessions   enable row level security;
alter table handoff.estop_checks      enable row level security;
alter table handoff.hardware_moves    enable row level security;
alter table handoff.ai_proposals      enable row level security;
alter table handoff.vision_detections enable row level security;
alter table handoff.meta              enable row level security;

revoke all on schema handoff from public, anon, authenticated;
revoke all on all tables in schema handoff from public, anon, authenticated;
alter default privileges in schema handoff revoke all on tables from public, anon, authenticated;

-- The backend's own least-privilege login. Set its password separately:
--   alter role handoff_app with password '<strong password>';
-- Pooler username: handoff_app.<project-ref>
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'handoff_app') then
    create role handoff_app login noinherit;
  end if;
end $$;

grant usage on schema handoff to handoff_app;
grant select, insert, update, delete on all tables in schema handoff to handoff_app;

-- RLS stays on: only handoff_app gets a policy, so anon/authenticated still see nothing.
do $$
declare t text;
begin
  foreach t in array array['macros', 'calibration', 'device_sessions', 'estop_checks', 'hardware_moves', 'ai_proposals', 'vision_detections', 'meta'] loop
    if not exists (select 1 from pg_policies where schemaname = 'handoff' and tablename = t and policyname = 'handoff_app_all') then
      execute format('create policy handoff_app_all on handoff.%I for all to handoff_app using (true) with check (true)', t);
    end if;
  end loop;
end $$;
