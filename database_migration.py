#!/usr/bin/env python3
"""
Database migration script to add new columns:
- route_count to locations table
- node_count to routes table

Run this script after updating the models to migrate existing data.
"""

from database.config import SessionLocal, engine
from sqlalchemy import text

def migrate_database():
    """Add new columns to existing tables and fix route_segments primary key"""
    
    db = SessionLocal()
    try:
        print("Starting database migration...")
        
        # Add route_count column to locations table
        try:
            db.execute(text("ALTER TABLE locations ADD COLUMN route_count INTEGER DEFAULT 0"))
            print("✅ Added route_count column to locations table")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("⚠️  route_count column already exists in locations table")
            else:
                print(f"❌ Error adding route_count column: {e}")
        
        # Add node_count column to routes table
        try:
            db.execute(text("ALTER TABLE routes ADD COLUMN node_count INTEGER DEFAULT 0"))
            print("✅ Added node_count column to routes table")
        except Exception as e:
            if "already exists" in str(e).lower() or "duplicate column" in str(e).lower():
                print("⚠️  node_count column already exists in routes table")
            else:
                print(f"❌ Error adding node_count column: {e}")
        
        # Fix route_segments table primary key structure
        print("\n🔧 Fixing route_segments table structure...")
        try:
            # Check if we need to recreate the table
            result = db.execute(text("""
                SELECT constraint_name 
                FROM information_schema.table_constraints 
                WHERE table_name = 'route_segments' 
                AND constraint_type = 'PRIMARY KEY'
            """))
            pk_info = result.fetchone()
            
            if pk_info:
                print("   Backing up route_segments data...")
                # Create backup table
                db.execute(text("""
                    CREATE TABLE route_segments_backup AS 
                    SELECT * FROM route_segments
                """))
                
                print("   Dropping and recreating route_segments table...")
                # Drop the table
                db.execute(text("DROP TABLE route_segments CASCADE"))
                
                # Recreate with new structure
                db.execute(text("""
                    CREATE TABLE route_segments (
                        route_id INTEGER NOT NULL,
                        segment_osm_id VARCHAR NOT NULL,
                        segment_order INTEGER NOT NULL,
                        direction BOOLEAN NOT NULL,
                        PRIMARY KEY (route_id, segment_order),
                        FOREIGN KEY (route_id) REFERENCES routes(id),
                        FOREIGN KEY (segment_osm_id) REFERENCES road_segments(osm_id)
                    )
                """))
                
                print("   Restoring route_segments data...")
                # Restore data
                db.execute(text("""
                    INSERT INTO route_segments (route_id, segment_osm_id, segment_order, direction)
                    SELECT route_id, segment_osm_id, segment_order, direction
                    FROM route_segments_backup
                """))
                
                # Drop backup table
                db.execute(text("DROP TABLE route_segments_backup"))
                
                print("✅ Successfully updated route_segments table structure")
            else:
                print("⚠️  route_segments table structure already correct or doesn't exist")
                
        except Exception as e:
            print(f"❌ Error fixing route_segments table: {e}")
            # Try to restore from backup if it exists
            try:
                db.execute(text("DROP TABLE route_segments"))
                db.execute(text("ALTER TABLE route_segments_backup RENAME TO route_segments"))
                print("⚠️  Restored route_segments from backup due to error")
            except:
                pass
        
        # Update existing locations with current route counts
        try:
            result = db.execute(text("""
                UPDATE locations 
                SET route_count = (
                    SELECT COUNT(*) 
                    FROM routes 
                    WHERE routes.location_id = locations.id
                )
                WHERE route_count = 0 OR route_count IS NULL
            """))
            print(f"✅ Updated route counts for {result.rowcount} locations")
        except Exception as e:
            print(f"❌ Error updating route counts: {e}")
        
        # Commit changes
        db.commit()
        print("✅ Database migration completed successfully!")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        db.rollback()
        
    finally:
        db.close()

if __name__ == "__main__":
    migrate_database() 