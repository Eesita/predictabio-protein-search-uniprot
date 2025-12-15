-- Add methods_validation_status column to metadata_final_filtered table
-- This column tracks which papers passed the methods validation (recombinant protein production classification)

ALTER TABLE metadata_final_filtered 
ADD COLUMN IF NOT EXISTS methods_validation_status TEXT;

-- Column values:
-- 'yes' - Paper is about recombinant protein production (PASSED)
-- 'no' - Paper is not about recombinant protein production (FAILED)
-- 'error' - Error occurred during classification
-- 'insufficient_data' - Not enough data to classify
-- NULL - Not yet validated

-- Optional: Add comment to document the column
COMMENT ON COLUMN metadata_final_filtered.methods_validation_status IS 
'Methods validation status from methods_validator_node. Values: yes (passed), no (failed), error, insufficient_data, or NULL (not validated)';

