#!/usr/bin/env python3
"""
Test script to verify location removal functionality with user road segment cleanup.
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.config import SessionLocal
from database.utils import (
    create_user, 
    create_location, 
    get_location_cleanup_stats,
    remove_location,
    get_user_road_segments,
    sync_user_road_segments
)
from database.models import User, Location, Route, RoadSegment, UserRoadSegment

def test_location_removal():
    """Test the location removal functionality"""
    print("=== Testing Location Removal with User Road Segment Cleanup ===\n")
    
    db = SessionLocal()
    try:
        # Create a test user
        print("1. Creating test user...")
        user = create_user(db, "test_user_location_removal")
        if not user:
            print("❌ Failed to create test user")
            return False
        print(f"✅ Created user: {user.username} (ID: {user.id})")
        
        # Create two test locations
        print("\n2. Creating test locations...")
        location1 = create_location(db, user.id, "Home", "123 Main St, Test City", 40.7128, -74.0060, 2000)
        location2 = create_location(db, user.id, "Work", "456 Business Ave, Test City", 40.7589, -73.9851, 1500)
        
        if not location1 or not location2:
            print("❌ Failed to create test locations")
            return False
        print(f"✅ Created locations: {location1.name} (ID: {location1.id}), {location2.name} (ID: {location2.id})")
        
        # Create some test road segments
        print("\n3. Creating test road segments...")
        segments = []
        for i in range(5):
            segment = RoadSegment(
                segment_id=f"test_segment_{i}",
                osm_id=f"osm_{i}",
                node_u=f"node_{i}_u",
                node_v=f"node_{i}_v",
                name=f"Test Street {i}",
                road_type="residential",
                length=100.0 + i * 50
            )
            db.add(segment)
            segments.append(segment)
        
        db.commit()
        print(f"✅ Created {len(segments)} test road segments")
        
        # Create test routes for both locations
        print("\n4. Creating test routes...")
        from database.utils import create_route_with_segments
        
        # Route for location 1
        route1 = create_route_with_segments(
            db, location1.id, "Route 1", "Test route 1",
            ["test_segment_0", "test_segment_1"], [True, True]
        )
        
        # Route for location 2 (shares some segments with location 1)
        route2 = create_route_with_segments(
            db, location2.id, "Route 2", "Test route 2",
            ["test_segment_1", "test_segment_2", "test_segment_3"], [True, True, True]
        )
        
        if not route1 or not route2:
            print("❌ Failed to create test routes")
            return False
        print(f"✅ Created routes: {route1.name} (ID: {route1.id}), {route2.name} (ID: {route2.id})")
        
        # Sync user road segments
        print("\n5. Syncing user road segments...")
        sync_user_road_segments(db, user.id)
        user_segments = get_user_road_segments(db, user.id)
        print(f"✅ Synced {len(user_segments)} user road segments")
        
        # Mark some segments as run
        print("\n6. Marking some segments as run...")
        run_count = 0
        for i, segment in enumerate(user_segments):
            if i < 2:  # Mark first 2 segments as run
                segment.has_been_run = True
                run_count += 1
        
        db.commit()
        print(f"✅ Marked {run_count} segments as run")
        
        # Test cleanup stats for location 1
        print("\n7. Testing cleanup stats for location 1...")
        cleanup_stats = get_location_cleanup_stats(db, location1.id)
        if cleanup_stats:
            print(f"✅ Cleanup stats for {cleanup_stats['location_name']}:")
            print(f"   - Routes: {cleanup_stats['route_count']}")
            print(f"   - Route segments: {cleanup_stats['route_segment_count']}")
            print(f"   - Unique segments: {cleanup_stats['unique_segments_used']}")
            print(f"   - Unused user segments: {cleanup_stats['unused_user_segments']}")
            
            if cleanup_stats['unused_user_segments'] > 0:
                print("   - Unused segment details:")
                for seg in cleanup_stats['unused_user_segments_details']:
                    status = "Run" if seg['has_been_run'] else "Not Run"
                    print(f"     * {seg['name']} ({status})")
        else:
            print("❌ Failed to get cleanup stats")
            return False
        
        # Remove location 1
        print(f"\n8. Removing location 1 ({location1.name})...")
        if remove_location(db, location1.id):
            print("✅ Successfully removed location 1")
        else:
            print("❌ Failed to remove location 1")
            return False
        
        # Check remaining user segments
        print("\n9. Checking remaining user segments...")
        remaining_user_segments = get_user_road_segments(db, user.id)
        print(f"✅ Remaining user segments: {len(remaining_user_segments)}")
        
        # Check that segments unique to location 1 were removed
        remaining_segment_ids = {seg.segment_id for seg in remaining_user_segments}
        expected_remaining = {"test_segment_1", "test_segment_2", "test_segment_3"}  # Segments used by location 2
        expected_removed = {"test_segment_0"}  # Segment only used by location 1
        
        print(f"   Expected remaining: {expected_remaining}")
        print(f"   Expected removed: {expected_removed}")
        print(f"   Actual remaining: {remaining_segment_ids}")
        
        if remaining_segment_ids == expected_remaining:
            print("✅ User segment cleanup working correctly!")
        else:
            print("❌ User segment cleanup not working as expected")
            return False
        
        # Test cleanup stats for location 2 (should show no unused segments)
        print("\n10. Testing cleanup stats for location 2...")
        cleanup_stats2 = get_location_cleanup_stats(db, location2.id)
        if cleanup_stats2:
            print(f"✅ Cleanup stats for {cleanup_stats2['location_name']}:")
            print(f"   - Routes: {cleanup_stats2['route_count']}")
            print(f"   - Route segments: {cleanup_stats2['route_segment_count']}")
            print(f"   - Unique segments: {cleanup_stats2['unique_segments_used']}")
            print(f"   - Unused user segments: {cleanup_stats2['unused_user_segments']}")
            
            if cleanup_stats2['unused_user_segments'] == 0:
                print("✅ Correctly shows no unused segments (all segments are used by this location)")
            else:
                print("❌ Incorrectly shows unused segments")
                return False
        else:
            print("❌ Failed to get cleanup stats for location 2")
            return False
        
        print("\n=== Test completed successfully! ===")
        return True
        
    except Exception as e:
        print(f"❌ Test failed with error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        db.close()

if __name__ == "__main__":
    success = test_location_removal()
    sys.exit(0 if success else 1) 