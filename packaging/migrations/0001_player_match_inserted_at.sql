-- Migration 0001 — add ``inserted_at`` to player_match
--
-- Run this once in the Supabase SQL editor (Database → SQL Editor → New
-- query, paste, Run) on the squad's existing project. New projects get
-- the column straight from packaging/supabase_schema.sql and don't need
-- this migration.
--
-- Why: CloudSync pulls each table incrementally by filtering
-- ``inserted_at > <watermark>``. ``replays`` and ``players`` already had
-- the column, but ``player_match`` did not — so its watermark could
-- never be saved and every sync re-downloaded the entire table. Once the
-- table grew to tens of MB, flaky connections began dropping that single
-- large response mid-stream (SSL UNEXPECTED_EOF_WHILE_READING), so new
-- player rows never landed locally and players went missing from the UI.
--
-- This migration is idempotent: safe to run more than once.

-- 1. Add the column. Existing rows get now() as a one-time backfill,
--    which is fine — the watermark just starts from "first sync after
--    migration" and pulls forward from there.
ALTER TABLE player_match
    ADD COLUMN IF NOT EXISTS inserted_at TIMESTAMPTZ NOT NULL DEFAULT now();

-- 2. Index it so the ``order=inserted_at`` + ``inserted_at=gt.…`` pulls
--    stay fast as the table grows.
CREATE INDEX IF NOT EXISTS idx_pm_inserted ON player_match(inserted_at);
