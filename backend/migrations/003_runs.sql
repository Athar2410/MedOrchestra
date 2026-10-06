-- Completed pipeline runs: case input, final report and the full SSE event stream,
-- so past cases can be listed, replayed in the UI, and used for evaluation.

create table if not exists runs (
    id          text primary key,
    created_at  timestamptz not null default now(),
    status      text not null,          -- completed | failed
    case_input  jsonb not null,
    report      jsonb,
    events      jsonb not null,
    error       text,
    duration_ms int
);

create index if not exists runs_created_at on runs (created_at desc);

-- Same as pubmed_chunks: block the Data API; the backend connects as the table owner.
alter table runs enable row level security;
