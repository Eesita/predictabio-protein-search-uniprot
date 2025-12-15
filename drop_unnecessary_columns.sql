-- PostgreSQL Migration Script: Drop Unnecessary Columns from Metadata Tables
-- Removes columns that are not needed to reduce memory usage and storage

-- Columns to drop:
-- title_clean, authors_clean, abstract_clean, doi_clean, pmid_clean
-- is_merged, merged_count, merge_type, original_indices, merge_timestamp
-- match_type, match_value, enriched_fields, original_sources
-- all_dois, all_pmids, deduplicated_at

-- Stage 1: metadata_fetched
ALTER TABLE IF EXISTS metadata_fetched 
    DROP COLUMN IF EXISTS title_clean,
    DROP COLUMN IF EXISTS authors_clean,
    DROP COLUMN IF EXISTS abstract_clean,
    DROP COLUMN IF EXISTS doi_clean,
    DROP COLUMN IF EXISTS pmid_clean,
    DROP COLUMN IF EXISTS is_merged,
    DROP COLUMN IF EXISTS merged_count,
    DROP COLUMN IF EXISTS merge_type,
    DROP COLUMN IF EXISTS original_indices,
    DROP COLUMN IF EXISTS merge_timestamp,
    DROP COLUMN IF EXISTS match_type,
    DROP COLUMN IF EXISTS match_value,
    DROP COLUMN IF EXISTS enriched_fields,
    DROP COLUMN IF EXISTS original_sources,
    DROP COLUMN IF EXISTS all_dois,
    DROP COLUMN IF EXISTS all_pmids,
    DROP COLUMN IF EXISTS deduplicated_at;

-- Stage 2: metadata_growth_factor_filtered
ALTER TABLE IF EXISTS metadata_growth_factor_filtered
    DROP COLUMN IF EXISTS title_clean,
    DROP COLUMN IF EXISTS authors_clean,
    DROP COLUMN IF EXISTS abstract_clean,
    DROP COLUMN IF EXISTS doi_clean,
    DROP COLUMN IF EXISTS pmid_clean,
    DROP COLUMN IF EXISTS is_merged,
    DROP COLUMN IF EXISTS merged_count,
    DROP COLUMN IF EXISTS merge_type,
    DROP COLUMN IF EXISTS original_indices,
    DROP COLUMN IF EXISTS merge_timestamp,
    DROP COLUMN IF EXISTS match_type,
    DROP COLUMN IF EXISTS match_value,
    DROP COLUMN IF EXISTS enriched_fields,
    DROP COLUMN IF EXISTS original_sources,
    DROP COLUMN IF EXISTS all_dois,
    DROP COLUMN IF EXISTS all_pmids,
    DROP COLUMN IF EXISTS deduplicated_at;

-- Stage 3: metadata_parameters_extracted
ALTER TABLE IF EXISTS metadata_parameters_extracted
    DROP COLUMN IF EXISTS title_clean,
    DROP COLUMN IF EXISTS authors_clean,
    DROP COLUMN IF EXISTS abstract_clean,
    DROP COLUMN IF EXISTS doi_clean,
    DROP COLUMN IF EXISTS pmid_clean,
    DROP COLUMN IF EXISTS is_merged,
    DROP COLUMN IF EXISTS merged_count,
    DROP COLUMN IF EXISTS merge_type,
    DROP COLUMN IF EXISTS original_indices,
    DROP COLUMN IF EXISTS merge_timestamp,
    DROP COLUMN IF EXISTS match_type,
    DROP COLUMN IF EXISTS match_value,
    DROP COLUMN IF EXISTS enriched_fields,
    DROP COLUMN IF EXISTS original_sources,
    DROP COLUMN IF EXISTS all_dois,
    DROP COLUMN IF EXISTS all_pmids,
    DROP COLUMN IF EXISTS deduplicated_at;

-- Stage 4: metadata_final_filtered
ALTER TABLE IF EXISTS metadata_final_filtered
    DROP COLUMN IF EXISTS title_clean,
    DROP COLUMN IF EXISTS authors_clean,
    DROP COLUMN IF EXISTS abstract_clean,
    DROP COLUMN IF EXISTS doi_clean,
    DROP COLUMN IF EXISTS pmid_clean,
    DROP COLUMN IF EXISTS is_merged,
    DROP COLUMN IF EXISTS merged_count,
    DROP COLUMN IF EXISTS merge_type,
    DROP COLUMN IF EXISTS original_indices,
    DROP COLUMN IF EXISTS merge_timestamp,
    DROP COLUMN IF EXISTS match_type,
    DROP COLUMN IF EXISTS match_value,
    DROP COLUMN IF EXISTS enriched_fields,
    DROP COLUMN IF EXISTS original_sources,
    DROP COLUMN IF EXISTS all_dois,
    DROP COLUMN IF EXISTS all_pmids,
    DROP COLUMN IF EXISTS deduplicated_at;

-- Verification: Show remaining columns for each table
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns
WHERE table_schema = 'public'
    AND table_name IN (
        'metadata_fetched',
        'metadata_growth_factor_filtered',
        'metadata_parameters_extracted',
        'metadata_final_filtered'
    )
ORDER BY table_name, ordinal_position;

