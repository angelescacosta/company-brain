-- T5: initial schema. Exact search only (no approximate vector index).

CREATE TABLE brain.documents (
    doc_id        text PRIMARY KEY,
    client        text NOT NULL,
    scope         text NOT NULL,
    doc_type      text NOT NULL,
    language      text NOT NULL CHECK (language IN ('en', 'es', 'mixed')),
    doc_date      date,
    status        text CHECK (status IN ('draft', 'signed')),
    supersedes    text REFERENCES brain.documents (doc_id),
    file_hash     text NOT NULL,
    availability  text NOT NULL CHECK (availability IN ('available', 'unavailable', 'pointer')),
    rel_path      text NOT NULL,
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_scope ON brain.documents (scope);

CREATE TABLE brain.index_meta (
    index_id      text PRIMARY KEY,
    model         text NOT NULL,
    revision      text NOT NULL,
    dimensions    integer NOT NULL CHECK (dimensions > 0),
    instruction   text NOT NULL,
    chunk_budget  integer NOT NULL CHECK (chunk_budget IN (300, 500, 800)),
    active        boolean NOT NULL DEFAULT false,
    complete      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX index_meta_one_active ON brain.index_meta (active) WHERE active;

CREATE TABLE brain.chunks (
    chunk_id      text PRIMARY KEY,
    index_id      text NOT NULL REFERENCES brain.index_meta (index_id) ON DELETE CASCADE,
    doc_id        text NOT NULL REFERENCES brain.documents (doc_id) ON DELETE CASCADE,
    text          text NOT NULL,
    label         text NOT NULL,
    parent_id     text,
    provenance    jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_spans  jsonb NOT NULL DEFAULT '[]'::jsonb,
    embedding     public.vector,
    tsv_en        tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    tsv_es        tsvector GENERATED ALWAYS AS (to_tsvector('spanish', text)) STORED,
    tsv_simple    tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED
);
CREATE INDEX chunks_index_doc ON brain.chunks (index_id, doc_id);
CREATE INDEX chunks_tsv_en ON brain.chunks USING gin (tsv_en);
CREATE INDEX chunks_tsv_es ON brain.chunks USING gin (tsv_es);
CREATE INDEX chunks_tsv_simple ON brain.chunks USING gin (tsv_simple);

CREATE TABLE brain.index_files (
    index_id      text NOT NULL REFERENCES brain.index_meta (index_id) ON DELETE CASCADE,
    doc_id        text NOT NULL REFERENCES brain.documents (doc_id) ON DELETE CASCADE,
    file_hash     text NOT NULL,
    status        text NOT NULL CHECK (status IN ('pending', 'complete', 'failed')),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (index_id, doc_id)
);

CREATE TABLE brain.ingest_runs (
    run_id         text PRIMARY KEY,
    trigger        text NOT NULL CHECK (trigger IN ('launcher', 'after_start', 'nightly', 'manual')),
    state          text NOT NULL CHECK (state IN ('running', 'completed', 'failed')),
    started_at     timestamptz NOT NULL DEFAULT now(),
    ended_at       timestamptz,
    added          integer NOT NULL DEFAULT 0,
    changed        integer NOT NULL DEFAULT 0,
    removed        integer NOT NULL DEFAULT 0,
    failed         integer NOT NULL DEFAULT 0,
    indexed_up_to  timestamptz
);

CREATE TABLE brain.file_status (
    rel_path      text PRIMARY KEY,
    status        text NOT NULL CHECK (
        status IN ('ok', 'pointer', 'unavailable', 'unsupported', 'unlisted', 'failed')),
    reason        text NOT NULL,
    size          bigint,
    checked_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE brain.tombstones (
    client        text PRIMARY KEY,
    created_at    timestamptz NOT NULL DEFAULT now()
);
