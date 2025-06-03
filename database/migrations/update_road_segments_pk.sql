-- Drop existing route_segments table since it references road_segments.id
DROP TABLE IF EXISTS route_segments;

-- Drop existing primary key constraint
ALTER TABLE road_segments DROP CONSTRAINT IF EXISTS road_segments_pkey;

-- Make osm_id the primary key
ALTER TABLE road_segments ADD PRIMARY KEY (osm_id);

-- Drop the id column since we don't need it anymore
ALTER TABLE road_segments DROP COLUMN IF EXISTS id;

-- Recreate route_segments table with osm_id reference
CREATE TABLE route_segments (
    route_id INTEGER REFERENCES routes(id) ON DELETE CASCADE,
    segment_osm_id VARCHAR REFERENCES road_segments(osm_id) ON DELETE CASCADE,
    segment_order INTEGER NOT NULL,
    direction BOOLEAN NOT NULL,
    PRIMARY KEY (route_id, segment_osm_id)
); 