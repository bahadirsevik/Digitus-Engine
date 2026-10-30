-- Pazar envanteri (SALT-OKUNUR). plan_pazar_dil_secimi.md Faz 0-4: dagitim oncesi sunucuda kosulur.
-- Kullanim: docker exec -i digitus_db psql -U digitus -d digitus_engine -f - < scripts/market_inventory.sql

\echo '== 1. Calisma pazar dagilimi (brand_profiles; NULL ayri satir) =='
SELECT default_geo_target_id AS geo, default_language_id AS lang, count(*) AS workspaces
FROM brand_profiles
GROUP BY 1, 2
ORDER BY workspaces DESC, geo, lang;

\echo '== 2. workspace_keywords geo/dil dagilimi (NULL dahil) =='
SELECT geo_target_id AS geo, language_id AS lang, count(*) AS rows
FROM workspace_keywords
GROUP BY 1, 2
ORDER BY rows DESC, geo, lang;

\echo '== 2b. NULL sayilari =='
SELECT count(*) AS total_rows,
       count(*) FILTER (WHERE geo_target_id IS NULL) AS geo_null,
       count(*) FILTER (WHERE language_id IS NULL) AS lang_null,
       count(*) FILTER (WHERE geo_target_id IS NULL AND language_id IS NULL) AS both_null
FROM workspace_keywords;

\echo '== 3. Acik degeri calismanin pazarindan FARKLI satirlar (NULL "bilinmiyor" sayilir, dahil degil) =='
SELECT count(*) FILTER (WHERE wk.geo_target_id IS NOT NULL
                          AND wk.geo_target_id IS DISTINCT FROM bp.default_geo_target_id) AS geo_differs,
       count(*) FILTER (WHERE wk.language_id IS NOT NULL
                          AND wk.language_id IS DISTINCT FROM bp.default_language_id) AS lang_differs,
       count(*) FILTER (WHERE (wk.geo_target_id IS NOT NULL
                               AND wk.geo_target_id IS DISTINCT FROM bp.default_geo_target_id)
                           OR (wk.language_id IS NOT NULL
                               AND wk.language_id IS DISTINCT FROM bp.default_language_id)) AS either_differs
FROM workspace_keywords wk
JOIN brand_profiles bp ON bp.id = wk.brand_profile_id;

\echo '== 4. Latin disi harf iceren kelimeler (Faz 2 rekey vekili): Kiril / Yunan =='
SELECT count(*) FILTER (WHERE keyword ~ '[Ѐ-ӿ]') AS cyrillic_keywords,
       count(*) FILTER (WHERE keyword ~ '[Ͱ-Ͽ]') AS greek_keywords,
       count(*) AS all_keywords
FROM keywords;

\echo '== 4b. Aynisi, yalniz bir calismaya bagli kelimeler =='
SELECT count(DISTINCT k.id) FILTER (WHERE k.keyword ~ '[Ѐ-ӿ]') AS cyrillic_in_workspaces,
       count(DISTINCT k.id) FILTER (WHERE k.keyword ~ '[Ͱ-Ͽ]') AS greek_in_workspaces
FROM workspace_keywords wk
JOIN keywords k ON k.id = wk.keyword_id;
