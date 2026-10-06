-- Bound the keyword side of hybrid_search.
--
-- At 50K rows, OR-ing every query term matched ~15K abstracts and ranking them all with
-- ts_rank_cd took ~7 s on a cold Supabase free-tier cache (each rank reads the row's
-- tsvector). Now: AND the terms first (short hypothesis queries usually match a small,
-- precise set); only if that finds nothing, OR them. Either way at most 300 matches are
-- ranked, so the cost no longer grows with the corpus.

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
    select nullif(plainto_tsquery('english', query_text)::text, '')::tsquery as tsq_and,
           nullif(replace(plainto_tsquery('english', query_text)::text, '&', '|'), '')::tsquery as tsq_or
),
semantic as (
    select c.pmid, row_number() over (order by c.embedding <#> query_embedding) as rank
    from pubmed_chunks c
    where c.embedding is not null
    order by c.embedding <#> query_embedding
    limit match_count * 3
),
and_matches as (
    select c.pmid, c.fts from pubmed_chunks c, q
    where q.tsq_and is not null and c.fts @@ q.tsq_and
    limit 300
),
or_matches as (
    select c.pmid, c.fts from pubmed_chunks c, q
    where not exists (select 1 from and_matches)
      and q.tsq_or is not null and c.fts @@ q.tsq_or
    limit 300
),
candidates as (
    select * from and_matches
    union all
    select * from or_matches
),
keyword as (
    select k.pmid, row_number() over (order by ts_rank_cd(k.fts, q.tsq_or) desc) as rank
    from candidates k, q
    order by ts_rank_cd(k.fts, q.tsq_or) desc
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
