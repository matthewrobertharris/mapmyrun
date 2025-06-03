-- Remove location_id foreign key constraint
ALTER TABLE road_segments DROP CONSTRAINT IF EXISTS road_segments_location_id_fkey;

-- Remove location_id column
ALTER TABLE road_segments DROP COLUMN IF EXISTS location_id;

-- Remove is_completed column
ALTER TABLE road_segments DROP COLUMN IF EXISTS is_completed; 