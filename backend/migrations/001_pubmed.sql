-- PubMed abstracts for the Diagnostician's retrieval (one abstract per chunk).
-- Idempotent: safe to re-run via `python -m pipelines.migrate`.

create extension if not exists vector;

create table if not exists pubmed_chunks (
    pmid        bigint primary key,
    title       text not null,
    abstract    text not null,
    journal     text,
    pub_year    int,
    mesh_terms  text[] not null default '{}',
    topic       text,            -- ingestion topic that selected this article
    -- MedCPT article embedding, half precision: 768 dims x 2 bytes keeps 50K rows
    -- well inside the Supabase free tier's 500 MB.
    embedding   halfvec(768),
    fts         tsvector generated always as (
                    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
                    setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
                ) stored,
    created_at  timestamptz not null default now()
);

-- Supabase exposes `public` tables through its Data API to holders of the publishable
-- key; RLS with no policies blocks that. The backend connects as the table owner, which
-- bypasses RLS.
alter table pubmed_chunks enable row level security;

-- MedCPT is trained for inner-product similarity, hence halfvec_ip_ops and `<#>`.
create index if not exists pubmed_chunks_embedding_hnsw
    on pubmed_chunks using hnsw (embedding halfvec_ip_ops);
create index if not exists pubmed_chunks_fts on pubmed_chunks using gin (fts);

-- Hybrid search: vector and keyword rankings fused with Reciprocal Rank Fusion.
-- The keyword side ORs the query's terms (an AND of a whole clinical vignette would
-- almost never match).
create or replace function hybrid_search(
    query_text text,
    query_embedding halfvec(768),
    match_count int default 10,
    rrf_k int default 60
)
returns table (
    pmid bigint,
    title text,
    abstract text,
    journal text,
    pub_year int,
    vector_rank bigint,
    keyword_rank bigint,
    rrf_score double precision
)
language sql stable
as $$
with q as (
    select nullif(replace(plainto_tsquery('english', query_text)::text, '&', '|'), '')::tsquery as tsq
),
semantic as (
    select c.pmid, row_number() over (order by c.embedding <#> query_embedding) as rank
    from pubmed_chunks c
    where c.embedding is not null
    order by c.embedding <#> query_embedding
    limit match_count * 3
),
keyword as (
    select c.pmid, row_number() over (order by ts_rank_cd(c.fts, q.tsq) desc) as rank
    from pubmed_chunks c, q
    where q.tsq is not null and c.fts @@ q.tsq
    order by ts_rank_cd(c.fts, q.tsq) desc
    limit match_count * 3
),
fused as (
    select coalesce(s.pmid, k.pmid) as pmid,
           s.rank as vector_rank,
           k.rank as keyword_rank,
           coalesce(1.0 / (rrf_k + s.rank), 0) + coalesce(1.0 / (rrf_k + k.rank), 0) as rrf_score
    from semantic s
    full outer join keyword k on s.pmid = k.pmid
)
select c.pmid, c.title, c.abstract, c.journal, c.pub_year, f.vector_rank, f.keyword_rank, f.rrf_score
from fused f
join pubmed_chunks c on c.pmid = f.pmid
order by f.rrf_score desc
limit match_count;
$$;
