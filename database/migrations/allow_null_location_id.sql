-- Allow NULL values in location_id column
ALTER TABLE road_segments ALTER COLUMN location_id DROP NOT NULL; 