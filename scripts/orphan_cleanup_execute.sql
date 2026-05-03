-- DESTRUCTIVE orphan cleanup.
--
-- Run only after:
-- 1. Creating a backup under backups/orphan_backup_<date>.sql
-- 2. Running scripts/orphan_cleanup_precheck.sql
-- 3. Showing counts to the user
-- 4. Receiving explicit manual approval
--
-- This script deletes rows whose keyword_id no longer exists in keywords.
-- Keep this transaction intact. Roll back if row counts differ from precheck.

BEGIN;

DELETE FROM channel_pools cp
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = cp.keyword_id
);

DELETE FROM intent_analysis ia
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = ia.keyword_id
);

DELETE FROM channel_candidates cc
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = cc.keyword_id
);

DELETE FROM keyword_scores ks
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = ks.keyword_id
);

-- Verify affected row counts before COMMIT in interactive use.
COMMIT;
