-- Read-only orphan/FK precheck for missing keywords.id foreign keys.
-- Safe to run before cleanup. Does not modify data.

SELECT
    conrelid::regclass AS table_name,
    conname,
    confrelid::regclass AS ref_table,
    confdeltype
FROM pg_constraint
WHERE contype = 'f'
  AND conrelid::regclass::text IN (
      'channel_pools',
      'intent_analysis',
      'channel_candidates',
      'keyword_scores',
      'pre_filter_results',
      'keyword_relevance'
  )
ORDER BY 1, 2;

SELECT 'channel_pools' AS tbl, COUNT(*) AS orphan_count
FROM channel_pools cp
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = cp.keyword_id
)
UNION ALL
SELECT 'intent_analysis', COUNT(*)
FROM intent_analysis ia
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = ia.keyword_id
)
UNION ALL
SELECT 'channel_candidates', COUNT(*)
FROM channel_candidates cc
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = cc.keyword_id
)
UNION ALL
SELECT 'keyword_scores', COUNT(*)
FROM keyword_scores ks
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = ks.keyword_id
)
UNION ALL
SELECT 'pre_filter_results', COUNT(*)
FROM pre_filter_results pf
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = pf.keyword_id
)
UNION ALL
SELECT 'keyword_relevance', COUNT(*)
FROM keyword_relevance kr
WHERE NOT EXISTS (
    SELECT 1 FROM keywords k WHERE k.id = kr.keyword_id
);
