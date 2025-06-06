import logging
import osmnx as ox
import folium
import gpxpy
import os
from geopy.distance import geodesic
from visualization import visualize_solution, visualize_road_network, visualize_classification
from metrics import print_metrics
from graph_processing import (
    get_coordinates,
    get_road_network,
    calculate_edge_cover,
    calculate_solution_metrics,
    analyze_excluded_edges
)
from strava_analysis import classify_road_segments, match_points_to_edges
from not_run_analysis import analyze_not_run_edges
from database.config import SessionLocal
from database.utils import (
    create_user,
    create_location,
    add_road_segment,
    sync_user_road_segments,
    get_user_by_username,
    get_user_segment_stats,
    remove_location,
    create_route_with_segments,
    clear_database,
    get_activity_by_strava_id,
    create_activity,
    update_segment_run_status,
    get_activity_by_id,
    get_user_road_segments,
    get_user_activities,
    get_activity_gps_points
)
from database.models import User, RoadSegment, Route, route_segments, Location
from datetime import datetime, timezone
from collections import defaultdict
import csv
import shutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def ensure_output_folders():
    """Create output folders if they don't exist"""
    folders = ['visualizations', 'debug']
    
    for folder in folders:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"Created folder: {folder}")
        else:
            print(f"Folder already exists: {folder}")
    
    return True

def register_user(db, username):
    """
    Register a new user in the system
    
    Args:
        db: SQLAlchemy session
        username: Unique username for the new user
        
    Returns:
        User object if registration successful, None if username already exists
    """
    try:
        # Check if username already exists
        existing_user = get_user_by_username(db, username)
        if existing_user:
            logging.warning(f"Username '{username}' already exists")
            return None
        
        # Create new user
        user = create_user(db, username)
        db.commit()  # Commit the transaction
        logging.info(f"User '{username}' registered successfully")
        return user
        
    except Exception as e:
        db.rollback()  # Rollback on error
        logging.error(f"Error registering user: {str(e)}")
        return None

def add_user_location(db, user_id, name, address, max_distance):
    """
    Add a new location for a user
    
    Args:
        db: SQLAlchemy session
        user_id: ID of the user
        name: Name for this location (e.g., "Home", "Work")
        address: Full address of the location
        max_distance: Maximum distance in meters for routes from this location
        
    Returns:
        Location object if successful, None if failed
    """
    try:
        # Convert address to coordinates
        coordinates = get_coordinates(address)
        if not coordinates:
            logging.error(f"Could not geocode address: {address}")
            return None
            
        lat, lon = coordinates
        
        # Create location
        location = create_location(
            db,
            user_id=user_id,
            name=name,
            address=address,
            latitude=lat,
            longitude=lon,
            max_distance=max_distance
        )
        
        db.commit()  # Commit the transaction
        logging.info(f"Location '{name}' added successfully for user {user_id}")
        return location
        
    except Exception as e:
        db.rollback()  # Rollback on error
        logging.error(f"Error adding location: {str(e)}")
        return None

def display_menu(username):
    """Display the main menu options"""
    print(f"\n=== MapMyRun Menu - Welcome {username}! ===")
    print("1. View and re-process locations")
    print("2. Add new location")
    print("3. Remove location")
    print("4. Visualize location data")
    print("5. Load GPS data from Strava file")
    print("6. Analyze GPS data and update road segments")
    print("7. Visualize your running progress")
    print("8. Diagnose and fix road segment issues")
    print("9. Visualize all road segments in database")
    print("10. Visualize specific route (debugging)")
    print("11. Clean up and organize files")
    print("0. Reset database (WARNING: Deletes all data)")
    print("00. Exit")
    return input("\nSelect an option: ")

def select_location(db, user):
    """Simply select a location for visualization or other operations"""
    if not user.locations:
        print("\nYou don't have any locations yet.")
        return None
        
    print("\n=== Your Locations ===")
    for i, loc in enumerate(user.locations, 1):
        route_info = f" ({getattr(loc, 'route_count', 'Unknown')} routes)" if hasattr(loc, 'route_count') else ""
        print(f"{i}. {loc.name} ({loc.address}){route_info}")
    
    choice = input("\nSelect a location number (or 0 to go back): ")
    if choice == "0":
        return None
    
    try:
        index = int(choice) - 1
        if 0 <= index < len(user.locations):
            return user.locations[index]
    except ValueError:
        pass
    
    print("Invalid selection.")
    return None

def handle_locations(db, user):
    """Display and manage user locations with re-processing option"""
    if not user.locations:
        print("\nYou don't have any locations yet.")
        return None
        
    print("\n=== Your Locations ===")
    for i, loc in enumerate(user.locations, 1):
        route_info = f" ({getattr(loc, 'route_count', 'Unknown')} routes)" if hasattr(loc, 'route_count') else ""
        print(f"{i}. {loc.name} ({loc.address}){route_info}")
    
    choice = input("\nSelect a location number to re-process (or 0 to go back): ")
    if choice == "0":
        return None
    
    try:
        index = int(choice) - 1
        if 0 <= index < len(user.locations):
            selected_location = user.locations[index]
            
            print(f"\nSelected location: {selected_location.name}")
            print(f"Address: {selected_location.address}")
            print(f"Current routes: {getattr(selected_location, 'route_count', 'Unknown')}")
            
            # Confirm re-processing
            confirm = input(f"\nDo you want to re-process this location? This will:")
            print("  - Remove all existing routes for this location")
            print("  - Regenerate the road network and routes")
            print("  - Update user road segments")
            choice = input("Continue? (y/n): ")
            
            if choice.lower() == 'y':
                print(f"\nRe-processing location: {selected_location.name}")
                
                # Store location details before re-processing
                location_id = selected_location.id
                location_name = selected_location.name
                
                try:
                    # Delete existing routes and their segments explicitly
                    print("Removing existing routes...")
                    existing_routes = db.query(Route).filter(Route.location_id == location_id).all()
                    route_ids = [route.id for route in existing_routes]
                    
                    # First delete route_segments for these routes
                    if route_ids:
                        print(f"Removing route segments for {len(route_ids)} routes...")
                        deleted_segments = db.execute(
                            route_segments.delete().where(route_segments.c.route_id.in_(route_ids))
                        )
                        print(f"Deleted {deleted_segments.rowcount} route segments")
                    
                    # Then delete the routes themselves
                    print("Removing routes...")
                    for route in existing_routes:
                        db.delete(route)
                    
                    # Reset route count
                    selected_location.route_count = 0
                    db.commit()
                    print(f"Removed {len(existing_routes)} existing routes")
                    
                    # Re-process the location to generate new routes
                    print("Generating new routes...")
                    if process_location_routes(db, selected_location):
                        print(f"✅ Successfully re-processed {location_name}!")
                        
                        # Refresh the location to get updated route count
                        db.refresh(selected_location)
                        print(f"Generated {selected_location.route_count} new routes")
                        
                        # Show some route details
                        new_routes = db.query(Route).filter(Route.location_id == location_id).all()
                        if new_routes:
                            print(f"\nRoute details:")
                            for i, route in enumerate(new_routes[:5], 1):  # Show first 5
                                node_info = f" ({route.node_count} nodes)" if hasattr(route, 'node_count') and route.node_count else ""
                                distance_info = f" - {route.distance/1000:.1f}km" if route.distance else ""
                                print(f"  {i}. {route.name}{node_info}{distance_info}")
                            
                            if len(new_routes) > 5:
                                print(f"  ... and {len(new_routes) - 5} more routes")
                        
                        return selected_location
                    else:
                        print(f"❌ Failed to re-process {location_name}")
                        return None
                        
                except Exception as e:
                    print(f"Error during re-processing: {str(e)}")
                    db.rollback()
                    return None
            else:
                print("Re-processing cancelled.")
                return selected_location
    except ValueError:
        pass
    
    print("Invalid selection.")
    return None

def create_normalized_segment_id(osm_id, node_u, node_v):
    """
    Create a normalized segment ID by sorting the nodes.
    This ensures that both directions of the same road create the same segment ID.
    """
    # Convert to strings for consistent comparison
    u_str = str(node_u)
    v_str = str(node_v)
    
    # Sort nodes to ensure consistent ordering
    if u_str < v_str:
        return f"{osm_id}_{u_str}_{v_str}" if osm_id else f"no_osm_{u_str}_{v_str}"
    else:
        return f"{osm_id}_{v_str}_{u_str}" if osm_id else f"no_osm_{v_str}_{u_str}"

def store_road_segments(db, G, location_id):
    """Store road segments from the network graph"""
    print("Storing road segments...")
    segments_added = 0
    segments_updated = 0
    segments_skipped = 0
    bidirectional_count = 0
    error_count = 0
    
    # Track processed OSM IDs to handle bidirectional roads
    processed_osm_ids = set()
    
    total_edges = len(G.edges())
    print(f"Total edges in graph: {total_edges}")
    
    for u, v, data in G.edges(data=True):
        try:
            # Get coordinates for this edge
            if 'geometry' in data:
                coords = [(float(p[1]), float(p[0])) for p in data['geometry'].coords]
            else:
                coords = [(float(G.nodes[n]['y']), float(G.nodes[n]['x'])) for n in [u, v]]
                
            # Get edge properties and convert numpy types to Python native types
            osm_id = str(data.get('osmid', ''))  # Keep original OSM ID
            # Create unique segment identifier: osmid_nodeU_nodeV
            segment_id = create_normalized_segment_id(osm_id, u, v)
            name = str(data.get('name', ''))
            road_type = str(data.get('highway', ''))
            length = float(data.get('length', 0))
            
            # Store node information for the segment in normalized order
            u_str = str(u)
            v_str = str(v)
            if u_str < v_str:
                node_u = u_str
                node_v = v_str
            else:
                node_u = v_str
                node_v = u_str
            
            # Note: No longer skipping bidirectional roads since composite IDs make each direction unique
            
            # Check if this is a bidirectional road we've already processed
            # OSM IDs are the same for both directions of the same road
            # NOTE: Disabled because composite IDs make each direction unique
            # if osm_id in processed_osm_ids:
            #     bidirectional_count += 1
            #     continue
            #     
            # processed_osm_ids.add(osm_id)
            
            # Check if segment already exists (now using segment_id as unique identifier)
            existing_segment = (
                db.query(RoadSegment)
                .filter(RoadSegment.segment_id == segment_id)
                .first()
            )
            
            if existing_segment:
                # Update existing segment if needed
                if (existing_segment.name != name or 
                    existing_segment.road_type != road_type or 
                    abs(existing_segment.length - length) > 0.01):  # Use small threshold for float comparison
                    
                    existing_segment.name = name
                    existing_segment.road_type = road_type
                    existing_segment.length = length
                    existing_segment.last_updated = datetime.utcnow()
                    segments_updated += 1
                else:
                    segments_skipped += 1
            else:
                # Create new segment with all the new fields
                segment = add_road_segment(
                    db,
                    osm_id,           # Original OSM ID
                    segment_id,       # Unique segment identifier
                    node_u,           # Start node
                    node_v,           # End node
                    name,
                    road_type,
                    coords,
                    length
                )
                if segment:
                    segments_added += 1
                else:
                    print(f"Warning: Failed to add segment {segment_id} (no error thrown)")
                    error_count += 1
                    
        except Exception as e:
            print(f"Error processing edge {u}->{v}: {str(e)}")
            error_count += 1
            continue
    
    try:
        db.commit()
        print("\nRoad segment processing summary:")
        print(f"Total edges in graph: {total_edges}")
        print(f"Bidirectional edges skipped: {bidirectional_count} (disabled - using composite IDs)")
        print(f"New segments added: {segments_added}")
        print(f"Existing segments updated: {segments_updated}")
        print(f"Unchanged segments skipped: {segments_skipped}")
        print(f"Errors encountered: {error_count}")
        print(f"Total unique segments: {segments_added + segments_updated + segments_skipped}")
        return segments_added + segments_updated
        
    except Exception as e:
        db.rollback()
        print(f"Error committing road segments: {str(e)}")
        return 0

def process_location_routes(db, location):
    """Calculate and store routes for a location"""
    print(f"\nProcessing routes for location: {location.name}")
    
    center_point = (location.latitude, location.longitude)
    distance = 2000  # Distance for road network retrieval
    max_distance = location.max_distance * 2  # Double the max distance to account for out and back
    
    try:
        # Get road network
        print("Retrieving road network...")
        G = get_road_network(center_point, distance)
        
        
        # Store road segments first
        num_segments = store_road_segments(db, G, location.id)
        if num_segments == 0:
            print("Warning: No road segments were stored")
            return False
        
        
        # Find nearest node to start point
        start_node = ox.nearest_nodes(G, center_point[1], center_point[0])
        
        # Calculate edge cover solution
        print(f"Calculating routes (max distance: {max_distance/1000:.1f}km)...")
        cycles = calculate_edge_cover(G, start_node, max_distance)
        
        # Export cycles to CSV for debugging
        print("Exporting cycles data for debugging...")
        try:
            import csv
            
            # Ensure debug folder exists
            ensure_output_folders()
            
            cycles_filename = os.path.join('debug', f"cycles_{location.name.replace(' ', '_')}.csv")
            
            with open(cycles_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['cycle_number', 'edge_number', 'node_from', 'node_to', 'osm_id', 'expected_segment_id', 'edge_exists_in_graph', 'length', 'name', 'highway']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                total_edges = 0
                for cycle_num, cycle in enumerate(cycles, 1):
                    edge_num = 0
                    for u, v in zip(cycle[:-1], cycle[1:]):
                        edge_num += 1
                        total_edges += 1
                        
                        # Check if edge exists in graph
                        edge_exists = G.has_edge(u, v)
                        
                        # Get edge data if it exists
                        osm_id = ''
                        name = ''
                        highway = ''
                        length = 0
                        if edge_exists:
                            edge_data = G.edges[u, v, 0]
                            osm_id = str(edge_data.get('osmid', ''))
                            name = str(edge_data.get('name', ''))
                            highway = str(edge_data.get('highway', ''))
                            length = float(edge_data.get('length', 0))
                        
                        # Generate expected segment ID
                        expected_segment_id = create_normalized_segment_id(osm_id, u, v)
                        
                        writer.writerow({
                            'cycle_number': cycle_num,
                            'edge_number': edge_num,
                            'node_from': u,
                            'node_to': v,
                            'osm_id': osm_id,
                            'expected_segment_id': expected_segment_id,
                            'edge_exists_in_graph': edge_exists,
                            'length': length,
                            'name': name,
                            'highway': highway
                        })
                
                print(f"Exported {len(cycles)} cycles with {total_edges} total edges to '{cycles_filename}'")
                
        except Exception as e:
            print(f"Warning: Failed to export cycles data: {str(e)}")
        
        # Visualize the initial solution before storing routes
        #print("\nVisualizing initial solution...")
        #metrics = calculate_solution_metrics(G, cycles, start_node, max_distance)
        #visualize_solution(G, cycles, center_point, metrics, output_file='initial_solution.html')
        #print("Initial solution saved to 'initial_solution.html'")
        
        # Store each cycle as a route
        print("\nStoring routes...")
        routes_created = 0
        
        for i, cycle in enumerate(cycles, 1):
            # Get the road segments for this cycle
            segment_ids = []
            segment_directions = []
            
            print(f"  Processing cycle {i} with {len(cycle)} nodes...")
            edges_processed = 0
            edges_found = 0
            edges_skipped = 0
            
            for u, v in zip(cycle[:-1], cycle[1:]):
                edges_processed += 1
                
                try:
                    # Get edge data - handle MultiDiGraph properly
                    if G.has_edge(u, v):
                        edge_data = G.edges[u, v, 0]  # Get first edge data
                    else:
                        print(f"    Warning: Edge {u}->{v} not found in graph")
                        edges_skipped += 1
                        continue
                    
                    # Generate the same segment_id used during storage
                    osm_id = str(edge_data.get('osmid', ''))
                    segment_id = create_normalized_segment_id(osm_id, u, v)
                
                    # Find the corresponding road segment using segment_id
                    segment = (
                        db.query(RoadSegment)
                        .filter(RoadSegment.segment_id == segment_id)
                        .first()
                    )
                    
                    if segment:
                        # Always add the segment - routes can traverse same segment multiple times
                        segment_ids.append(segment.segment_id)
                        
                        # Determine direction based on node order
                        # This is a simplified direction - could be enhanced to check actual geometry
                        segment_directions.append(True)  # For now, always forward
                        edges_found += 1
                    else:
                        print(f"    Warning: Road segment not found for segment ID {segment_id}")
                        edges_skipped += 1
                        
                except Exception as e:
                    print(f"    Error processing edge {u}->{v}: {str(e)}")
                    edges_skipped += 1
                    continue
            
            print(f"    Edges: {edges_processed} processed, {edges_found} found, {edges_skipped} skipped")
            
            if segment_ids:
                try:
                    # Create route name and description
                    route_name = f"Route {i} from {location.name}"
                    description = f"Generated route {i} of {len(cycles)} for location {location.name}"
                    
                    # Store the route with its segments
                    route = create_route_with_segments(
                        db,
                        location.id,
                        route_name,
                        description,
                        segment_ids,
                        segment_directions
                    )
                    
                    if route:
                        # Update the route with node count for debugging
                        route.node_count = len(cycle)
                        routes_created += 1
                        print(f"    ✓ Created route with {len(segment_ids)} segments (from {len(cycle)} nodes)")
                        db.commit()  # Commit after each successful route creation
                    else:
                        print(f"    ✗ Failed to create route")
                except Exception as e:
                    print(f"Error creating route {i}: {str(e)}")
                    db.rollback()  # Rollback on error
                    continue
            else:
                print(f"    ✗ No valid segments found for cycle {i}")
        
        print(f"Successfully created {routes_created} routes!")
        
        # Update location with route count
        location.route_count = routes_created
        db.commit()
        
        # Sync user road segments
        print("Syncing user road segments...")
        sync_user_road_segments(db, location.user_id)
        
        # Export routes to CSV for debugging
        print("Exporting routes data for debugging...")
        try:
            import csv
            
            # Ensure debug folder exists
            ensure_output_folders()
            
            routes_filename = os.path.join('debug', f"routes_debug_{location.name.replace(' ', '_')}.csv")
            
            with open(routes_filename, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['route_id', 'route_name', 'total_segments', 'segments_with_geometry', 'segments_without_geometry', 'total_length_m', 'will_visualize']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for route in routes:
                    segments = (
                        db.query(RoadSegment, route_segments.c.direction, route_segments.c.segment_order)
                        .join(route_segments)
                        .filter(route_segments.c.route_id == route.id)
                        .order_by(route_segments.c.segment_order)
                        .all()
                    )
                    
                    total_segments = len(segments)
                    segments_with_geo = sum(1 for seg, _, _ in segments if seg.geometry)
                    segments_without_geo = total_segments - segments_with_geo
                    total_length = sum(seg.length for seg, _, _ in segments if seg.geometry)
                    will_visualize = segments_with_geo > 0
                    
                    writer.writerow({
                        'route_id': route.id,
                        'route_name': route.name,
                        'total_segments': total_segments,
                        'segments_with_geometry': segments_with_geo,
                        'segments_without_geometry': segments_without_geo,
                        'total_length_m': total_length,
                        'will_visualize': will_visualize
                    })
                
                print(f"Exported route debugging data to '{routes_filename}'")
                
        except Exception as e:
            print(f"Warning: Failed to export routes debug data: {str(e)}")
        
        return True
        
    except Exception as e:
        print(f"Error processing routes: {str(e)}")
        db.rollback()  # Ensure we rollback on any error
        return False

def add_new_location(db, user):
    """Handle adding a new location"""
    print("\n=== Add New Location ===")
    
    while True:
        location_name = input("Enter a name for your location (e.g., 'Home'): ")
        address = input("Enter the address for this location: ")
        
        try:
            max_distance = float(input("Enter the maximum distance for routes from this location (in meters): "))
        except ValueError:
            print("Please enter a valid number for the distance.")
            continue
            
        print("\nLooking up address coordinates...")
        location = add_user_location(db, user.id, location_name, address, max_distance)
        
        if location:
            print(f"\nLocation '{location_name}' added successfully!")
            
            # Calculate and store routes for the new location
            print("Calculating routes...")
            if process_location_routes(db, location):
                print("Routes have been calculated and stored successfully!")
            else:
                print("Warning: Failed to calculate routes for this location.")
                
            db.refresh(user)
            break
        else:
            retry = input("\nFailed to add location. Would you like to try again with a different address? (y/n): ")
            if retry.lower() != 'y':
                break
    
    return location

def remove_user_location(db, user):
    """Handle removing a location"""
    if not user.locations:
        print("\nYou don't have any locations to remove.")
        return
        
    print("\n=== Remove Location ===")
    print("Your locations:")
    for i, loc in enumerate(user.locations, 1):
        print(f"{i}. {loc.name} ({loc.address})")
    
    while True:
        choice = input("\nSelect location to remove (0 to cancel): ")
        if choice == "0":
            return
            
        try:
            index = int(choice) - 1
            if 0 <= index < len(user.locations):
                location = user.locations[index]
                
                # Confirm deletion
                confirm = input(f"\nAre you sure you want to remove '{location.name}'? This will also remove all routes for this location. (y/n): ")
                if confirm.lower() == 'y':
                    if remove_location(db, location.id):
                        print(f"\nLocation '{location.name}' has been removed.")
                        db.refresh(user)
                    else:
                        print("\nFailed to remove location.")
                return
                
        except ValueError:
            pass
            
        print("Invalid selection. Please try again.")

def visualize_location_data(db, location):
    """Visualize road network and routes for a location"""
    print(f"\nVisualizing data for location: {location.name}")
    
    try:
        # Get all routes for this location
        routes = db.query(Route).filter(Route.location_id == location.id).all()
        
        if not routes:
            print("No routes found for this location.")
            print("Try re-processing this location to generate routes.")
            return
        
        print(f"Found {len(routes)} routes for this location")
        
        # Debug: Check route-segments relationships
        print("\n🔍 DEBUGGING ROUTE-SEGMENTS RELATIONSHIPS:")
        routes_with_no_segments = []
        routes_with_segments = []
        
        for route in routes:
            # Count segments in route_segments table
            segment_count = (
                db.query(route_segments)
                .filter(route_segments.c.route_id == route.id)
                .count()
            )
            
            if segment_count == 0:
                routes_with_no_segments.append(route)
                print(f"  ❌ Route {route.id} ({route.name}) has 0 segments in route_segments table")
            else:
                routes_with_segments.append(route)
                print(f"  ✅ Route {route.id} ({route.name}) has {segment_count} segments")
        
        print(f"\nRoute-Segments Summary:")
        print(f"  Routes with segments: {len(routes_with_segments)}")
        print(f"  Routes missing segments: {len(routes_with_no_segments)}")
        
        if routes_with_no_segments:
            print(f"\n⚠️  CRITICAL: {len(routes_with_no_segments)} routes have no segments!")
            print("  This explains why they don't render. The route_segments table is missing entries.")
            for route in routes_with_no_segments[:3]:  # Show first 3
                print(f"    - Route {route.id}: {route.name}")
        
        # Get all unique segments used by these routes
        all_route_segments = set()
        route_segment_data = {}
        
        for route in routes:
            segments = (
                db.query(RoadSegment, route_segments.c.direction, route_segments.c.segment_order)
                .join(route_segments)
                .filter(route_segments.c.route_id == route.id)
                .order_by(route_segments.c.segment_order)
                .all()
            )
            
            # Debug: Check if route has segments
            print(f"DEBUG: Route {route.id} ({route.name}) has {len(segments)} segments")
            
            if segments:
                route_segment_data[route.id] = segments
                for segment, _, _ in segments:
                    all_route_segments.add(segment.segment_id)
            else:
                print(f"WARNING: Route {route.id} ({route.name}) has no segments!")
        
        print(f"Found {len(all_route_segments)} unique segments across all routes")
        print(f"Routes with segments: {len(route_segment_data)}/{len(routes)}")
        
        # Check geometry data
        segments_with_geometry = 0
        segments_without_geometry = 0
        routes_with_issues = []
        
        for route_id, segments in route_segment_data.items():
            route_geometry_issues = 0
            for segment, _, _ in segments:
                if segment.geometry:
                    segments_with_geometry += 1
                else:
                    segments_without_geometry += 1
                    route_geometry_issues += 1
            
            if route_geometry_issues > 0:
                route_name = next(r.name for r in routes if r.id == route_id)
                routes_with_issues.append(f"Route {route_id} ({route_name}): {route_geometry_issues} segments missing geometry")
        
        print(f"Segments with geometry: {segments_with_geometry}")
        print(f"Segments without geometry: {segments_without_geometry}")
        
        if routes_with_issues:
            print(f"\nRoutes with geometry issues:")
            for issue in routes_with_issues[:5]:  # Show first 5
                print(f"  {issue}")
            if len(routes_with_issues) > 5:
                print(f"  ... and {len(routes_with_issues) - 5} more routes with issues")
        
        if segments_with_geometry == 0:
            print("❌ No segments have geometry data. Cannot create visualization.")
            return
        
        # Create visualization using Folium
        from geoalchemy2.shape import to_shape
        import folium
        import numpy as np
        
        print("Creating route visualization...")
        
        # Calculate map center from all segments
        all_coords = []
        
        for route_id, segments in route_segment_data.items():
            for segment, _, _ in segments:
                if segment.geometry:
                    try:
                        segment_line = to_shape(segment.geometry)
                        if hasattr(segment_line, 'coords'):
                            for coord in segment_line.coords:
                                all_coords.append((coord[0], coord[1]))  # Based on debug: geometry is (lat, lon)
                    except Exception as e:
                        print(f"  Warning: Error processing geometry for segment {segment.segment_id}: {e}")
                        continue
        
        if not all_coords:
            print("❌ No valid segment geometries found.")
            return
        
        # Calculate center point
        coords_array = np.array(all_coords)
        center_lat = np.mean(coords_array[:, 0])
        center_lon = np.mean(coords_array[:, 1])
        
        print(f"Creating map centered at ({center_lat:.4f}, {center_lon:.4f})")
        
        # Create map
        m = folium.Map(
            location=[center_lat, center_lon], 
            zoom_start=14,
            tiles='OpenStreetMap'
        )
        
        # Add routes to map with different colors
        colors = ['#FF0000', '#00FF00', '#0000FF', '#FF00FF', '#FFFF00', '#00FFFF', 
                 '#FF8000', '#8000FF', '#FF0080', '#80FF00', '#0080FF', '#FF8080', 
                 '#80FF80', '#8080FF', '#FF4000', '#40FF00', '#4000FF', '#FF0040',
                 '#00FF40', '#0040FF', '#FF2020', '#20FF20', '#2020FF', '#FFB000',
                 '#B0FF00', '#B000FF', '#FF00B0', '#00FFB0', '#00B0FF', '#C0C0C0']
        
        total_route_distance = 0
        routes_visualized = 0
        segments_visualized = 0
        
        # Create feature groups for each route (enables layer control)
        route_layers = {}
        
        # Track rendering statistics
        rendering_stats = {
            'routes_attempted': 0,
            'routes_with_segments': 0,
            'routes_rendered_to_map': 0,
            'total_polylines_created': 0,
            'routes_skipped_no_geometry': 0,
            'coordinate_errors': 0,
            'feature_groups_created': 0,
            'feature_groups_added_to_map': 0
        }
        
        print(f"\n🔍 MULTI-ROUTE VISUALIZATION DEBUG:")
        print(f"Processing {len(route_segment_data)} routes for visualization...")
        
        for i, (route_id, segments) in enumerate(route_segment_data.items()):
            rendering_stats['routes_attempted'] += 1
            color = colors[i % len(colors)]
            route = next(r for r in routes if r.id == route_id)
            
            print(f"\n--- Route {route_id} Processing ---")
            print(f"Route name: {route.name}")
            print(f"Color assigned: {color}")
            print(f"Segments to process: {len(segments)}")
            
            # Create a feature group for this route
            try:
                route_layer = folium.FeatureGroup(name=f"Route {route_id} ({len(segments)} segments)")
                route_layers[route_id] = route_layer
                rendering_stats['feature_groups_created'] += 1
                print(f"✅ Feature group created: {route_layer}")
            except Exception as e:
                print(f"❌ ERROR creating feature group: {str(e)}")
                continue
            
            route_distance = 0
            route_segments_added = 0
            route_segments_skipped = 0
            polylines_created_for_route = 0
            
            rendering_stats['routes_with_segments'] += 1
            
            for segment, direction, order in segments:
                if not segment.geometry:
                    route_segments_skipped += 1
                    continue
                    
                try:
                    segment_line = to_shape(segment.geometry)
                    
                    if hasattr(segment_line, 'coords'):
                        coords = [(coord[0], coord[1]) for coord in segment_line.coords]  # (lat, lon)
                        
                        # Validate coordinates
                        valid_coords = True
                        for lat, lon in coords:
                            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                valid_coords = False
                                rendering_stats['coordinate_errors'] += 1
                                print(f"    WARNING: Invalid coordinates for segment {segment.segment_id}: ({lat}, {lon})")
                                break
                        
                        if not valid_coords:
                            route_segments_skipped += 1
                            continue
                        
                        # Create popup text
                        popup_text = f"""
                        <b>Route {route_id} - Segment {order}</b><br>
                        {segment.name or 'Unnamed Road'}<br>
                        Segment ID: {segment.segment_id}<br>
                        OSM ID: {segment.osm_id}<br>
                        Type: {segment.road_type or 'Unknown'}<br>
                        Length: {segment.length:.0f}m<br>
                        Direction: {'Forward' if direction else 'Reverse'}
                        """
                        
                        # Add line to the route's feature group instead of directly to map
                        polyline = folium.PolyLine(
                            locations=coords,
                            color=color,
                            weight=4,
                            opacity=0.7,
                            popup=folium.Popup(popup_text, max_width=300)
                        )
                        polyline.add_to(route_layer)  # Add to route layer, not main map
                        
                        route_distance += segment.length
                        route_segments_added += 1
                        segments_visualized += 1
                        polylines_created_for_route += 1
                        rendering_stats['total_polylines_created'] += 1
                        
                except Exception as e:
                    print(f"    Warning: Error processing segment {segment.segment_id}: {str(e)}")
                    route_segments_skipped += 1
                    continue
            
            if route_segments_added > 0:
                total_route_distance += route_distance
                routes_visualized += 1
                print(f"    ✓ Added {route_segments_added} segments ({route_distance/1000:.1f}km) - {polylines_created_for_route} polylines created")
                if route_segments_skipped > 0:
                    print(f"    ⚠️  Skipped {route_segments_skipped} segments (no geometry)")
                
                # Add the route layer to the map
                try:
                    print(f"    🔧 Adding feature group to map...")
                    print(f"       Feature group type: {type(route_layer)}")
                    print(f"       Feature group name: {route_layer.layer_name if hasattr(route_layer, 'layer_name') else 'Unknown'}")
                    print(f"       Polylines in group: {polylines_created_for_route}")
                    
                    route_layer.add_to(m)
                    rendering_stats['routes_rendered_to_map'] += 1
                    rendering_stats['feature_groups_added_to_map'] += 1
                    print(f"    ✅ Route layer added to map successfully")
                    
                    # Verify the layer was actually added
                    try:
                        # Check if the layer appears in the map's children
                        layer_found_in_map = False
                        for child in m._children.values():
                            if child == route_layer:
                                layer_found_in_map = True
                                break
                        
                        if layer_found_in_map:
                            print(f"    ✅ Layer verified in map children")
                        else:
                            print(f"    ⚠️  Layer NOT found in map children")
                    except Exception as e:
                        print(f"    ⚠️  Could not verify layer in map: {str(e)}")
                    
                    # If route had some segments skipped, provide detail
                    if route_segments_skipped > 0:
                        print(f"    🔍 PARTIAL ROUTE ISSUES for Route {route_id}:")
                        print(f"      Successfully added: {route_segments_added} segments")
                        print(f"      Skipped segments: {route_segments_skipped}")
                        
                        # Show details of a few skipped segments
                        skipped_count = 0
                        for segment, direction, order in segments:
                            if not segment.geometry and skipped_count < 2:  # Show first 2 skipped
                                print(f"      Skipped segment {order}: {segment.segment_id} ({segment.name or 'Unnamed'}) - No geometry")
                                skipped_count += 1
                        
                        if route_segments_skipped > 2:
                            print(f"      ... and {route_segments_skipped - 2} more skipped segments")
                    
                except Exception as e:
                    print(f"    ❌ ERROR: Failed to add route layer to map: {str(e)}")
                    
                    # If route layer failed to add, this is a critical issue
                    print(f"    🔍 ROUTE LAYER FAILURE for Route {route_id}:")
                    print(f"      Polylines created: {polylines_created_for_route}")
                    print(f"      Route layer type: {type(route_layer)}")
                    print(f"      Error details: {str(e)}")
                    
                    import traceback
                    print(f"      Stack trace: {traceback.format_exc()}")
                    print("")
            else:
                print(f"    ✗ No segments could be visualized for route {route_id} (all {len(segments)} segments had issues)")
                rendering_stats['routes_skipped_no_geometry'] += 1
                
                # Debug failed routes in detail
                print(f"    🔍 DEBUGGING FAILED ROUTE {route_id}:")
                print(f"      Route name: {route.name}")
                print(f"      Total segments: {len(segments)}")
                
                for seg_idx, (segment, direction, order) in enumerate(segments[:3]):  # Check first 3 segments
                    print(f"      Segment {seg_idx + 1} (Order {order}):")
                    print(f"        Segment ID: {segment.segment_id}")
                    print(f"        Name: {segment.name or 'Unnamed'}")
                    print(f"        Has geometry: {segment.geometry is not None}")
                    
                    if segment.geometry:
                        try:
                            segment_line = to_shape(segment.geometry)
                            print(f"        Geometry type: {type(segment_line).__name__}")
                            
                            if hasattr(segment_line, 'coords'):
                                coords_list = list(segment_line.coords)
                                print(f"        Coordinates count: {len(coords_list)}")
                                
                                if coords_list:
                                    first_coord = coords_list[0]
                                    last_coord = coords_list[-1]
                                    print(f"        First coord: ({first_coord[0]:.6f}, {first_coord[1]:.6f})")
                                    print(f"        Last coord: ({last_coord[0]:.6f}, {last_coord[1]:.6f})")
                                    
                                    # Check if coordinates are in reasonable bounds
                                    for coord_idx, coord in enumerate(coords_list):
                                        lat, lon = coord[0], coord[1]
                                        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                            print(f"        ❌ INVALID COORD at index {coord_idx}: ({lat}, {lon})")
                                        elif abs(lat) < 0.001 or abs(lon) < 0.001:
                                            print(f"        ⚠️  SUSPICIOUS COORD at index {coord_idx}: ({lat}, {lon}) (too close to 0,0)")
                                else:
                                    print(f"        ❌ NO COORDINATES in geometry")
                            else:
                                print(f"        ❌ GEOMETRY has no coords attribute")
                                
                        except Exception as e:
                            print(f"        ❌ ERROR processing geometry: {str(e)}")
                    else:
                        print(f"        ❌ NO GEOMETRY DATA")
                
                if len(segments) > 3:
                    print(f"      ... and {len(segments) - 3} more segments")
                print(f"    🔍 END DEBUGGING ROUTE {route_id}")
                print("")
        
        # Add layer control to toggle routes on/off
        print(f"\n🔧 Adding layer control...")
        print(f"Feature groups created: {rendering_stats['feature_groups_created']}")
        print(f"Feature groups added to map: {rendering_stats['feature_groups_added_to_map']}")
        
        try:
            layer_control = folium.LayerControl(collapsed=False)
            layer_control.add_to(m)
            print(f"✅ Layer control added successfully")
            
            # List which routes should appear in layer control
            print(f"\nRoutes that should appear in layer control:")
            for route_id, route_layer in route_layers.items():
                route_name = next(r.name for r in routes if r.id == route_id)
                print(f"  - Route {route_id}: {route_name} (Layer: {route_layer.layer_name if hasattr(route_layer, 'layer_name') else 'Unknown'})")
                
        except Exception as e:
            print(f"❌ ERROR adding layer control: {str(e)}")
        
        # Add legend
        legend_html = f'''
        <div style="position: fixed; 
                    bottom: 10px; left: 10px; width: 180px; height: 120px;
                    border:2px solid grey; z-index:9999; background-color:white;
                    padding: 8px; font-size: 12px;">
            <h5 style="margin-top:0; margin-bottom:5px">{location.name}</h5>
            <p style="margin:2px 0"><b>{routes_visualized}/{len(routes)} routes</b></p>
            <p style="margin:2px 0"><b>{segments_visualized} segments</b></p>
            <p style="margin:2px 0"><b>{total_route_distance/1000:.1f}km total</b></p>
            <p style="font-size: 10px; margin:2px 0">Use layer control to toggle routes</p>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Add JavaScript for all on/off buttons in layer control
        all_toggle_js = '''
        <script>
        function addToggleButtons() {
            console.log('Attempting to add toggle buttons...');
            
            // Try multiple selectors to find the layer control
            var layerControl = document.querySelector('.leaflet-control-layers') || 
                              document.querySelector('.leaflet-control-layers-expanded') ||
                              document.querySelector('[class*="leaflet-control-layers"]');
            
            console.log('Layer control found:', layerControl);
            
            if (layerControl) {
                // Check if buttons already exist to avoid duplicates
                if (layerControl.querySelector('.toggle-buttons')) {
                    console.log('Buttons already exist');
                    return;
                }
                
                // Create buttons container
                var buttonDiv = document.createElement('div');
                buttonDiv.className = 'toggle-buttons';
                buttonDiv.style.cssText = 'margin: 8px 5px; text-align: center; border-top: 1px solid #ccc; padding-top: 5px;';
                
                // All On button
                var allOnBtn = document.createElement('button');
                allOnBtn.innerHTML = 'All On';
                allOnBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #4CAF50; color: white; border: none; border-radius: 3px;';
                allOnBtn.onclick = function() {
                    console.log('All On clicked');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Found checkboxes:', checkboxes.length);
                    checkboxes.forEach(function(cb) {
                        if (!cb.checked) {
                            console.log('Checking:', cb);
                            cb.click();
                        }
                    });
                };
                
                // All Off button
                var allOffBtn = document.createElement('button');
                allOffBtn.innerHTML = 'All Off';
                allOffBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #f44336; color: white; border: none; border-radius: 3px;';
                allOffBtn.onclick = function() {
                    console.log('All Off clicked');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Found checkboxes:', checkboxes.length);
                    checkboxes.forEach(function(cb) {
                        if (cb.checked) {
                            console.log('Unchecking:', cb);
                            cb.click();
                        }
                    });
                };
                
                // Debug button to check layer visibility
                var debugBtn = document.createElement('button');
                debugBtn.innerHTML = 'Debug';
                debugBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #2196F3; color: white; border: none; border-radius: 3px;';
                debugBtn.onclick = function() {
                    console.log('=== LAYER DEBUG INFO ===');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Total layer checkboxes found:', checkboxes.length);
                    
                    checkboxes.forEach(function(cb, index) {
                        var label = cb.parentElement.textContent.trim();
                        console.log(`Layer ${index + 1}: "${label}" - Checked: ${cb.checked}`);
                    });
                    
                    // Check for polylines on the map
                    var polylines = document.querySelectorAll('.leaflet-overlay-pane path');
                    console.log('Total polylines in DOM:', polylines.length);
                    
                    // Check for invisible polylines
                    var visiblePolylines = 0;
                    polylines.forEach(function(path) {
                        var style = window.getComputedStyle(path);
                        if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                            visiblePolylines++;
                        }
                    });
                    console.log('Visible polylines:', visiblePolylines);
                    console.log('=== END DEBUG INFO ===');
                };
                
                buttonDiv.appendChild(allOnBtn);
                buttonDiv.appendChild(allOffBtn);
                buttonDiv.appendChild(debugBtn);
                
                // Try multiple insertion points
                var insertPoint = layerControl.querySelector('form') || 
                                 layerControl.querySelector('.leaflet-control-layers-list') ||
                                 layerControl;
                
                insertPoint.appendChild(buttonDiv);
                console.log('Buttons added successfully');
                return true;
            }
            console.log('Layer control not found');
            return false;
        }
        
        // Try multiple times with increasing delays
        setTimeout(addToggleButtons, 500);
        setTimeout(addToggleButtons, 1500);
        setTimeout(addToggleButtons, 3000);
        
        // Also try when the page is fully loaded
        window.addEventListener('load', function() {
            setTimeout(addToggleButtons, 1000);
        });
        </script>
        '''
        m.get_root().html.add_child(folium.Element(all_toggle_js))
        
        # Save the map
        # Ensure visualizations folder exists
        ensure_output_folders()
        
        output_file = os.path.join('visualizations', 'route_map.html')
        m.save(output_file)
        
        print(f"\n✓ Route visualization created successfully!")
        print(f"📊 Visualization Statistics:")
        print(f"  🗺️  Routes visualized: {routes_visualized}/{len(routes)}")
        print(f"  📍 Segments shown: {segments_visualized}")
        print(f"  📏 Total distance: {total_route_distance/1000:.1f}km")
        print(f"\n🔍 Detailed Rendering Statistics:")
        print(f"  📈 Routes attempted: {rendering_stats['routes_attempted']}")
        print(f"  📂 Routes with segments: {rendering_stats['routes_with_segments']}")
        print(f"  🎛️  Feature groups created: {rendering_stats['feature_groups_created']}")
        print(f"  🗺️  Feature groups added to map: {rendering_stats['feature_groups_added_to_map']}")
        print(f"  ✅ Routes rendered to map: {rendering_stats['routes_rendered_to_map']}")
        print(f"  📍 Total polylines created: {rendering_stats['total_polylines_created']}")
        print(f"  ❌ Routes skipped (no geometry): {rendering_stats['routes_skipped_no_geometry']}")
        print(f"  ⚠️  Coordinate errors: {rendering_stats['coordinate_errors']}")
        print(f"\n🗺️  Map saved as: {output_file}")
        print("   Open this file in your web browser to view the interactive map!")
        
        # Additional debugging info
        if rendering_stats['routes_rendered_to_map'] < len(routes):
            missing_routes = len(routes) - rendering_stats['routes_rendered_to_map']
            print(f"\n⚠️  POTENTIAL ISSUE: {missing_routes} routes exist but weren't rendered to map")
            print("   Possible causes:")
            print("   - Routes have no segments with geometry")
            print("   - Invalid coordinates in segments")
            print("   - Errors during route layer creation")
            print("   - Feature group creation/addition failures")
            
            # List the specific routes that failed
            print(f"\n📋 SPECIFIC ROUTES THAT FAILED TO RENDER:")
            rendered_route_ids = set()
            for route_id in route_segment_data.keys():
                # Check if this route was actually rendered
                route_found_in_layers = False
                for layer_route_id in route_layers.keys():
                    if layer_route_id == route_id:
                        route_found_in_layers = True
                        break
                
                if route_found_in_layers:
                    rendered_route_ids.add(route_id)
            
            failed_routes = [r for r in routes if r.id not in rendered_route_ids]
            for route in failed_routes:
                segments_count = len(db.query(route_segments).filter(route_segments.c.route_id == route.id).all())
                print(f"   - Route {route.id}: '{route.name}' ({segments_count} segments)")
        
        # Check for feature group vs route rendering mismatches
        if rendering_stats['feature_groups_created'] != rendering_stats['feature_groups_added_to_map']:
            mismatch = rendering_stats['feature_groups_created'] - rendering_stats['feature_groups_added_to_map']
            print(f"\n⚠️  FEATURE GROUP MISMATCH: {mismatch} feature groups created but not added to map")
            print("   This suggests some routes failed during the map addition step")
        
        if rendering_stats['total_polylines_created'] == 0:
            print(f"\n❌ CRITICAL: No polylines were created at all!")
            print("   This suggests a fundamental issue with geometry processing")
        
        print(f"\n🎛️  Layer Control: Use the layer control in the top-right of the map to toggle routes on/off")
        print(f"   If routes appear in layer control but aren't visible, try:")
        print(f"   1. Zooming out to ensure routes are in view")
        print(f"   2. Checking if routes are hidden behind other routes")
        print(f"   3. Using browser developer tools to check for JavaScript errors")
        
    except Exception as e:
        print(f"\nError creating route visualization: {str(e)}")
        import traceback
        traceback.print_exc()

def list_available_gpx_files(strava_folder='strava'):
    """List all available GPX files in the Strava folder"""
    if not os.path.exists(strava_folder):
        return []
    
    gpx_files = []
    for filename in os.listdir(strava_folder):
        if filename.endswith('.gpx'):
            file_path = os.path.join(strava_folder, filename)
            # Get file modification time
            mtime = os.path.getmtime(file_path)
            file_date = datetime.fromtimestamp(mtime)
            gpx_files.append({
                'filename': filename,
                'path': file_path,
                'date': file_date
            })
    
    # Sort by date (newest first)
    gpx_files.sort(key=lambda x: x['date'], reverse=True)
    return gpx_files

def load_gpx_file_as_activity(db, user, gpx_file_path):
    """
    Load a GPX file and store it as an Activity with GPS points
    
    Args:
        db: SQLAlchemy session
        user: User object
        gpx_file_path: Path to the GPX file
        
    Returns:
        Activity object if successful, None if failed
    """
    try:
        print(f"Loading GPX file: {gpx_file_path}")
        
        with open(gpx_file_path, 'r') as gpx_file:
            gpx = gpxpy.parse(gpx_file)
        
        # Extract basic activity info
        activity_name = "Unknown Activity"
        activity_type = "Run"  # Default to Run
        start_time = None
        gps_points_data = []
        
        # Process tracks
        for track in gpx.tracks:
            if track.name:
                activity_name = track.name
            if track.type:
                activity_type = track.type.capitalize()
                
            for segment in track.segments:
                for point in segment.points:
                    if point.latitude is not None and point.longitude is not None:
                        # Validate coordinates
                        if -90 <= point.latitude <= 90 and -180 <= point.longitude <= 180:
                            # Convert to timezone-aware datetime if needed
                            point_time = point.time
                            if point_time and point_time.tzinfo is None:
                                point_time = point_time.replace(tzinfo=timezone.utc)
                            
                            if start_time is None and point_time:
                                start_time = point_time
                            
                            gps_point = {
                                'latitude': float(point.latitude),
                                'longitude': float(point.longitude),
                                'elevation': float(point.elevation) if point.elevation is not None else None,
                                'timestamp': point_time
                            }
                            gps_points_data.append(gps_point)
        
        if not gps_points_data:
            print("No valid GPS points found in the file")
            return None
        
        # Calculate basic statistics
        distance = None
        duration = None
        elevation_gain = None
        average_speed = None
        
        # Calculate distance and elevation gain
        if len(gps_points_data) > 1:
            total_distance = 0
            total_elevation_gain = 0
            
            for i in range(1, len(gps_points_data)):
                prev_point = gps_points_data[i-1]
                curr_point = gps_points_data[i]
                
                # Calculate distance between consecutive points
                point_distance = geodesic(
                    (prev_point['latitude'], prev_point['longitude']),
                    (curr_point['latitude'], curr_point['longitude'])
                ).meters
                total_distance += point_distance
                
                # Calculate elevation gain
                if (prev_point['elevation'] is not None and 
                    curr_point['elevation'] is not None):
                    elev_diff = curr_point['elevation'] - prev_point['elevation']
                    if elev_diff > 0:
                        total_elevation_gain += elev_diff
            
            distance = total_distance
            elevation_gain = total_elevation_gain
        
        # Calculate duration
        if (start_time and gps_points_data[-1]['timestamp']):
            end_time = gps_points_data[-1]['timestamp']
            duration = (end_time - start_time).total_seconds()
            
            # Calculate average speed
            if distance and duration > 0:
                average_speed = distance / duration
        
        # Generate unique Strava ID based on activity data from GPX file
        # Use start time, activity name, and type to create a unique identifier
        safe_name = activity_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:50]  # Limit length and make safe
        safe_type = activity_type.replace(" ", "_")
        
        if start_time:
            time_str = start_time.strftime('%Y%m%d_%H%M%S')
            strava_id = f"{safe_type}_{safe_name}_{time_str}"
        else:
            # Fallback if no timestamp available - use name and type with a hash of GPS points
            import hashlib
            gps_hash = hashlib.md5(str(gps_points_data[:5]).encode()).hexdigest()[:8]  # Use first 5 points for hash
            strava_id = f"{safe_type}_{safe_name}_{gps_hash}"
        
        # Check if activity already exists
        existing_activity = get_activity_by_strava_id(db, strava_id)
        if existing_activity:
            print(f"Activity from {activity_name} already exists in the database")
            return existing_activity
        
        # Create the activity
        activity = create_activity(
            db,
            user_id=user.id,
            strava_id=strava_id,
            name=activity_name,
            activity_type=activity_type,
            start_time=start_time or datetime.now(timezone.utc),
            gps_points_data=gps_points_data,
            distance=distance,
            duration=duration,
            elevation_gain=elevation_gain,
            average_speed=average_speed
        )
        
        if activity:
            print(f"Successfully loaded activity: {activity_name}")
            print(f"- Type: {activity_type}")
            print(f"- GPS Points: {len(gps_points_data)}")
            print(f"- Distance: {distance/1000:.2f} km" if distance else "- Distance: Unknown")
            print(f"- Duration: {duration/60:.1f} minutes" if duration else "- Duration: Unknown")
            print(f"- Elevation Gain: {elevation_gain:.1f} m" if elevation_gain else "- Elevation Gain: Unknown")
            
            # Update road segment run status based on this activity
            update_segment_run_status(db, user.id, activity.id)
            print("Updated road segment run status based on this activity")
            
        return activity
        
    except Exception as e:
        print(f"Error loading GPX file: {str(e)}")
        db.rollback()
        import traceback
        traceback.print_exc()
        return None

def handle_load_strava_gps_data(db, user):
    """Handle loading GPS data from Strava files"""
    print("\n=== Load GPS Data from Strava File ===")
    
    # List available GPX files
    gpx_files = list_available_gpx_files()
    
    if not gpx_files:
        print("\nNo GPX files found in the 'strava' folder.")
        print("Please place your Strava GPX files in the 'strava' directory and try again.")
        return
    
    print(f"\nFound {len(gpx_files)} GPX files:")
    for i, file_info in enumerate(gpx_files, 1):
        print(f"{i}. {file_info['filename']} (Modified: {file_info['date'].strftime('%Y-%m-%d %H:%M:%S')})")
    
    print(f"A. Load ALL {len(gpx_files)} files")
    
    while True:
        choice = input(f"\nSelect a file to load (1-{len(gpx_files)}, A for all, or 0 to go back): ")
        
        if choice == "0":
            return
        
        if choice.upper() == "A":
            # Load all files
            print(f"\nLoading all {len(gpx_files)} GPX files...")
            successful_loads = 0
            failed_loads = 0
            skipped_loads = 0
            
            for i, file_info in enumerate(gpx_files, 1):
                print(f"\n[{i}/{len(gpx_files)}] Processing {file_info['filename']}...")
                
                # Try to extract activity data from GPX for ID generation
                try:
                    with open(file_info['path'], 'r') as gpx_file:
                        gpx = gpxpy.parse(gpx_file)
                        
                        # Extract basic activity info
                        activity_name = "Unknown Activity"
                        activity_type = "Run"  # Default to Run
                        start_time = None
                        
                        # Process tracks
                        for track in gpx.tracks:
                            if track.name:
                                activity_name = track.name
                            if track.type:
                                activity_type = track.type.capitalize()
                                
                            for segment in track.segments:
                                for point in segment.points:
                                    if point.time:
                                        start_time = point.time
                                        if start_time.tzinfo is None:
                                            start_time = start_time.replace(tzinfo=timezone.utc)
                                        break
                                if start_time:
                                    break
                            if start_time:
                                break
                        
                        # Generate unique Strava ID based on activity data from GPX file
                        safe_name = activity_name.replace(" ", "_").replace("/", "_").replace("\\", "_")[:50]
                        safe_type = activity_type.replace(" ", "_")
                        
                        if start_time:
                            time_str = start_time.strftime('%Y%m%d_%H%M%S')
                            strava_id = f"{safe_type}_{safe_name}_{time_str}"
                        else:
                            # Fallback - need to read some GPS points for hash
                            gps_points_sample = []
                            for track in gpx.tracks:
                                for segment in track.segments:
                                    for point in segment.points[:5]:  # Just first 5 points
                                        if point.latitude is not None and point.longitude is not None:
                                            gps_points_sample.append((point.latitude, point.longitude))
                            import hashlib
                            gps_hash = hashlib.md5(str(gps_points_sample).encode()).hexdigest()[:8]
                            strava_id = f"{safe_type}_{safe_name}_{gps_hash}"
                        
                        existing_activity = get_activity_by_strava_id(db, strava_id)
                        
                        if existing_activity:
                            skipped_loads += 1
                            print(f"  ⚠️  Skipped (already exists)")
                            continue
                            
                except Exception as e:
                    print(f"  ⚠️  Error checking existing activity: {str(e)}")
                
                activity = load_gpx_file_as_activity(db, user, file_info['path'])
                
                if activity:
                    successful_loads += 1
                    print(f"  ✓  Successfully loaded")
                else:
                    failed_loads += 1
                    print(f"  ✗  Failed to load")
            
            print(f"\n=== Batch Load Summary ===")
            print(f"Total files processed: {len(gpx_files)}")
            print(f"Successfully loaded: {successful_loads}")
            print(f"Skipped (already existed): {skipped_loads}")
            print(f"Failed to load: {failed_loads}")
            
            if successful_loads > 0:
                print(f"\n✓ {successful_loads} new activities have been added to your account!")
                print("Road segment run status has been updated for all new activities.")
            
            return
        
        try:
            index = int(choice) - 1
            if 0 <= index < len(gpx_files):
                selected_file = gpx_files[index]
                
                print(f"\nLoading {selected_file['filename']}...")
                activity = load_gpx_file_as_activity(db, user, selected_file['path'])
                
                if activity:
                    print(f"\n✓ Successfully loaded GPS data from {selected_file['filename']}")
                    
                    # Ask if user wants to load another file
                    another = input("\nWould you like to load another GPX file? (y/n): ")
                    if another.lower() != 'y':
                        break
                else:
                    print(f"\n✗ Failed to load GPS data from {selected_file['filename']}")
                    retry = input("Would you like to try a different file? (y/n): ")
                    if retry.lower() != 'y':
                        break
            else:
                print("Invalid selection. Please try again.")
                
        except ValueError:
            print("Invalid input. Please enter a number or 'A' for all files.")

def handle_analyze_gps_data(db, user):
    """Analyze GPS data from user activities and update road segment run status"""
    print("\n=== Analyze GPS Data and Update Road Segments ===")
    
    # Check if user has any activities
    activities = get_user_activities(db, user.id)
    if not activities:
        print("\nNo GPS activities found for your account.")
        print("Please load some GPS data first using menu option 5.")
        return
    
    print(f"Found {len(activities)} activities in your account.")
    
    # Get all user road segments
    print("Loading your road segments...")
    user_segments = get_user_road_segments(db, user.id)
    
    if not user_segments:
        print("\nNo road segments found for your account.")
        print("Please set up a location and generate routes first using menu options 2 and 4.")
        return
    
    print(f"Found {len(user_segments)} road segments to analyze.")
    
    try:
        # Collect all GPS points from user activities
        print("Collecting GPS points from all activities...")
        all_gps_points = []
        
        for activity in activities:
            gps_points = get_activity_gps_points(db, activity.id)
            for point in gps_points:
                all_gps_points.append((point.latitude, point.longitude))
        
        if not all_gps_points:
            print("No GPS points found in activities.")
            return
        
        print(f"Collected {len(all_gps_points)} GPS points from {len(activities)} activities")
        
        # Simple deduplication
        print("Deduplicating GPS points...")
        seen = set()
        deduplicated_points = []
        for point in all_gps_points:
            if point not in seen:
                seen.add(point)
                deduplicated_points.append(point)
        
        print(f"Using {len(deduplicated_points)} unique GPS points for analysis")
        
        # Analyze each road segment against GPS points
        print("Analyzing GPS coverage for each road segment...")
        
        from geoalchemy2.shape import to_shape
        import numpy as np
        from scipy.spatial import cKDTree
        
        # Create spatial index for GPS points for efficient matching
        gps_points_array = np.array(deduplicated_points)
        gps_tree = cKDTree(gps_points_array)
        
        updates_made = 0
        segments_marked_run = 0
        total_analyzed = 0
        segments_with_geometry = 0
        segments_already_run = 0
        segments_no_geometry = 0
        
        # Debug: Check first few GPS points and segments
        print(f"\nDEBUG: First 5 GPS points: {deduplicated_points[:5]}")
        print(f"DEBUG: GPS points range - Lat: {min(p[0] for p in deduplicated_points):.6f} to {max(p[0] for p in deduplicated_points):.6f}")
        print(f"DEBUG: GPS points range - Lon: {min(p[1] for p in deduplicated_points):.6f} to {max(p[1] for p in deduplicated_points):.6f}")
        
        for segment in user_segments:
            total_analyzed += 1
            
            # Show progress every 50 segments
            if total_analyzed % 50 == 0 or total_analyzed == len(user_segments):
                print(f"  Progress: {total_analyzed}/{len(user_segments)} segments analyzed...")
            
            try:
                # Skip if already marked as run
                if segment.has_been_run:
                    segments_already_run += 1
                    continue
                
                # Get segment geometry
                if not segment.geometry:
                    segments_no_geometry += 1
                    continue
                
                segments_with_geometry += 1
                segment_line = to_shape(segment.geometry)
                
                # Debug: Print details for first few segments
                if total_analyzed <= 3:
                    print(f"\nDEBUG: Segment {total_analyzed} ({segment.segment_id}):")
                    print(f"  Name: {segment.name}")
                    print(f"  Length: {segment.length}m")
                    print(f"  Geometry type: {type(segment_line)}")
                    if hasattr(segment_line, 'coords'):
                        coords_list = list(segment_line.coords)
                        print(f"  Coords: {coords_list[:2]}...{coords_list[-2:] if len(coords_list) > 4 else ''}")
                        # Based on debug output, geometry appears to be stored as (lat, lon) not standard (lon, lat)
                        print(f"  Coord range - Lat: {min(c[0] for c in coords_list):.6f} to {max(c[0] for c in coords_list):.6f}")
                        print(f"  Coord range - Lon: {min(c[1] for c in coords_list):.6f} to {max(c[1] for c in coords_list):.6f}")
                
                # Sample points along the segment for analysis
                segment_length = segment_line.length  # This is in degrees, approximate
                if segment_length == 0:
                    continue
                
                # Sample points along the segment (every ~10 meters equivalent in degrees)
                sample_distance_degrees = 10 / 111000  # Approximate conversion
                num_samples = max(3, int(segment_length / sample_distance_degrees))
                
                sampled_points = []
                for i in range(num_samples):
                    fraction = i / (num_samples - 1) if num_samples > 1 else 0
                    point = segment_line.interpolate(fraction, normalized=True)
                    # If geometry is stored as (lat, lon), then point.x = lat, point.y = lon
                    sampled_points.append((point.x, point.y))  # (lat, lon)
                
                if not sampled_points:
                    continue
                
                # Debug: Show sampled points for first few segments
                if total_analyzed <= 3:
                    print(f"  Sampled {len(sampled_points)} points: {sampled_points[:2]}...{sampled_points[-2:] if len(sampled_points) > 4 else ''}")
                
                # Find nearest GPS points for each sampled point
                sampled_points_array = np.array(sampled_points)
                distances, indices = gps_tree.query(sampled_points_array, k=1)
                
                # Convert distances from degrees to meters (approximate)
                distances_meters = distances * 111000
                
                # Check if GPS points are close enough to consider the segment as "run"
                max_deviation_threshold = 15  # meters
                avg_deviation = np.mean(distances_meters)
                min_deviation = np.min(distances_meters)
                max_deviation = np.max(distances_meters)
                
                # Debug: Show deviation analysis for first few segments
                if total_analyzed <= 3:
                    print(f"  Deviations (m) - Min: {min_deviation:.1f}, Avg: {avg_deviation:.1f}, Max: {max_deviation:.1f}")
                    print(f"  Threshold: {max_deviation_threshold}m")
                    print(f"  Would mark as run: {avg_deviation <= max_deviation_threshold}")
                
                # If average deviation is within threshold, mark as run
                if avg_deviation <= max_deviation_threshold:
                    segment.has_been_run = True
                    # Find the earliest activity that could have covered this segment
                    earliest_activity = activities[0] if activities else None
                    segment.first_run_activity_id = earliest_activity.id if earliest_activity else None
                    segment.first_run_timestamp = earliest_activity.start_time if earliest_activity else None
                    segment.last_updated = datetime.utcnow()
                    updates_made += 1
                    segments_marked_run += 1
                    
                    if total_analyzed <= 10:  # Debug first 10 potential matches
                        print(f"  ✓ MARKED AS RUN: {segment.name} (avg deviation: {avg_deviation:.1f}m)")
                    
            except Exception as e:
                print(f"  Warning: Error analyzing segment {segment.segment_id}: {str(e)}")
                continue
        
        print(f"\nDEBUG: Analysis Summary:")
        print(f"  Total segments: {total_analyzed}")
        print(f"  Already marked as run: {segments_already_run}")
        print(f"  No geometry: {segments_no_geometry}")
        print(f"  With geometry: {segments_with_geometry}")
        print(f"  New segments marked as run: {segments_marked_run}")
        print(f"  Updates to commit: {updates_made}")
        
        # Commit the changes
        if updates_made > 0:
            print(f"\nCommitting {updates_made} updates to database...")
            db.commit()
            print(f"✓ Database commit successful!")
            print(f"✓ Analysis complete!")
            print(f"✓ Updated {updates_made} road segments")
            print(f"✓ {segments_marked_run} segments marked as run")
            
            # Show updated statistics
            stats = get_user_segment_stats(db, user.id)
            if stats:
                total_segments = stats.get('total_segments', 0)
                run_segments = stats.get('run_segments', 0)
                percentage = (run_segments / total_segments * 100) if total_segments > 0 else 0
                print(f"\n📊 Updated Statistics:")
                print(f"Total road segments: {total_segments}")
                print(f"Segments you've run: {run_segments} ({percentage:.1f}%)")
                print(f"Segments remaining: {total_segments - run_segments}")
        else:
            print("\n✓ Analysis complete!")
            print("✓ No new segments were marked as run")
            print("  (All qualifying segments may already be marked as run)")
            
            # Additional debugging if no segments were marked
            print(f"\nDEBUG: Possible reasons no segments were marked:")
            print(f"  - {segments_already_run} segments already marked as run")
            print(f"  - {segments_no_geometry} segments missing geometry")
            print(f"  - Remaining {segments_with_geometry} segments had GPS deviations > 15m")
            print(f"  - Check coordinate system compatibility between GPS and road data")
            
    except Exception as e:
        print(f"\nError during GPS analysis: {str(e)}")
        db.rollback()
        import traceback
        traceback.print_exc()

def visualize_user_progress(db, user):
    """Visualize user road segments with color coding based on run status"""
    print("\n=== Visualize Your Running Progress ===")
    
    # Get all user road segments
    user_segments = get_user_road_segments(db, user.id)
    
    if not user_segments:
        print("\nNo road segments found for your account.")
        print("This might be because:")
        print("1. You haven't set up any locations yet")
        print("2. User road segments haven't been synced properly")
        print("3. Road segments were created but have no geometry")
        
        # Check if user has locations
        if user.locations:
            print(f"\nYou have {len(user.locations)} location(s). Let me check if we can sync road segments...")
            
            # Try to sync road segments for each location
            for location in user.locations:
                print(f"\nChecking location: {location.name}")
                try:
                    # Get road network for this location
                    center_point = (location.latitude, location.longitude)
                    distance = location.max_distance
                    
                    print(f"  Retrieving road network (distance: {distance}m)...")
                    G = get_road_network(center_point, distance)
                    print(f"  Found {len(G.edges())} edges in road network")
                    
                    # Check how many road segments exist in database for this area
                    from database.models import RoadSegment
                    total_road_segments = db.query(RoadSegment).count()
                    print(f"  Total road segments in database: {total_road_segments}")
                    
                    if total_road_segments == 0:
                        print("  No road segments in database! Need to process location routes first.")
                        print("  Try menu option 2 -> Add location or re-process existing locations")
                        return
                    else:
                        print("  Road segments exist, trying to sync user road segments...")
                        sync_user_road_segments(db, user.id)
                        user_segments = get_user_road_segments(db, user.id)
                        print(f"  After sync: {len(user_segments)} user road segments")
                        break
                        
                except Exception as e:
                    print(f"  Error checking location {location.name}: {str(e)}")
                    continue
        else:
            print("Please set up a location first using menu option 2.")
            return
    
    if not user_segments:
        print("\nStill no user road segments found after sync attempt.")
        print("Please try:")
        print("1. Add a new location (menu option 2)")
        print("2. Or re-process existing locations to generate road segments")
        return
    
    print(f"Found {len(user_segments)} road segments to visualize.")
    
    # Debug: Show some basic statistics about segments
    segments_with_geometry = sum(1 for s in user_segments if s.geometry)
    segments_run = sum(1 for s in user_segments if s.has_been_run)
    segments_with_names = sum(1 for s in user_segments if s.name)
    
    print(f"\nDEBUG: Segment Statistics:")
    print(f"  Total segments: {len(user_segments)}")
    print(f"  With geometry: {segments_with_geometry}")
    print(f"  Without geometry: {len(user_segments) - segments_with_geometry}")
    print(f"  Marked as run: {segments_run}")
    print(f"  With names: {segments_with_names}")
    
    if segments_with_geometry == 0:
        print("\n⚠️  WARNING: No road segments have geometry data!")
        print("This means the road segments were created but geometry wasn't stored properly.")
        print("Try re-processing your locations to fix this.")
        return
    
    if len(user_segments) > 0:
        first_segment = user_segments[0]
        print(f"\nDEBUG: First segment example:")
        print(f"  Segment ID: {first_segment.segment_id}")
        print(f"  Name: {first_segment.name}")
        print(f"  Type: {first_segment.road_type}")
        print(f"  Length: {first_segment.length}")
        print(f"  Has geometry: {first_segment.geometry is not None}")
        print(f"  Has been run: {first_segment.has_been_run}")
    
    try:
        from geoalchemy2.shape import to_shape
        import folium
        import numpy as np
        
        # Calculate map center from all segments
        print("Calculating map center...")
        all_coords = []
        
        for segment in user_segments:
            if segment.geometry:
                try:
                    segment_line = to_shape(segment.geometry)
                    if hasattr(segment_line, 'coords'):
                        for coord in segment_line.coords:
                            all_coords.append((coord[0], coord[1]))  # Based on debug: geometry is (lat, lon)
                except Exception as e:
                    print(f"DEBUG: Error processing geometry for segment {segment.segment_id}: {e}")
                    continue
        
        if not all_coords:
            print("No valid segment geometries found.")
            return
        
        # Calculate center point
        coords_array = np.array(all_coords)
        center_lat = np.mean(coords_array[:, 0])
        center_lon = np.mean(coords_array[:, 1])
        
        print(f"Creating map centered at ({center_lat:.4f}, {center_lon:.4f})")
        print(f"DEBUG: Coordinate range - Lat: {np.min(coords_array[:, 0]):.6f} to {np.max(coords_array[:, 0]):.6f}")
        print(f"DEBUG: Coordinate range - Lon: {np.min(coords_array[:, 1]):.6f} to {np.max(coords_array[:, 1]):.6f}")
        
        # Create map
        m = folium.Map(
            location=[center_lat, center_lon], 
            zoom_start=14,
            tiles='OpenStreetMap'
        )
        
        # Create feature groups for different layers
        run_segments_layer = folium.FeatureGroup(name="✅ Run Segments")
        not_run_segments_layer = folium.FeatureGroup(name="❌ Not Run Segments")
        gps_tracks_layer = folium.FeatureGroup(name="🗺️ GPS Tracks")
        
        # Add layers to map
        run_segments_layer.add_to(m)
        not_run_segments_layer.add_to(m)
        gps_tracks_layer.add_to(m)
        
        # Get user's GPS activities for visualization
        print("Loading GPS tracks...")
        activities = get_user_activities(db, user.id)
        gps_tracks_added = 0
        
        if activities:
            # Add GPS tracks to the map
            colors_gps = ['blue', 'darkblue', 'lightblue', 'cadetblue', 'navy']
            
            for i, activity in enumerate(activities):
                try:
                    gps_points = get_activity_gps_points(db, activity.id)
                    if gps_points and len(gps_points) > 1:
                        # Create line from GPS points
                        gps_coords = [(point.latitude, point.longitude) for point in gps_points]
                        
                        # Create popup for GPS track
                        gps_popup_text = f"""
                        <b>{activity.name}</b><br>
                        Date: {activity.start_time.strftime('%Y-%m-%d')}<br>
                        Type: {activity.activity_type}<br>
                        Distance: {activity.distance/1000:.1f}km<br>
                        Duration: {activity.duration/60:.0f}min<br>
                        GPS Points: {len(gps_points)}
                        """
                        
                        # Add GPS track to GPS layer
                        folium.PolyLine(
                            locations=gps_coords,
                            color=colors_gps[i % len(colors_gps)],
                            weight=3,
                            opacity=0.8,
                            popup=folium.Popup(gps_popup_text, max_width=300)
                        ).add_to(gps_tracks_layer)
                        
                        gps_tracks_added += 1
                        
                except Exception as e:
                    print(f"  Warning: Error processing GPS track for activity {activity.id}: {str(e)}")
                    continue
        
        print(f"Added {gps_tracks_added} GPS tracks to map")
        
        # Count segments by status for legend
        run_segments = 0
        not_run_segments = 0
        error_segments = 0
        
        print("Adding road segments to map...")
        
        # Add each segment to the appropriate layer
        for segment in user_segments:
            try:
                if not segment.geometry:
                    error_segments += 1
                    continue
                
                segment_line = to_shape(segment.geometry)
                
                # Convert geometry to coordinates for folium
                if hasattr(segment_line, 'coords'):
                    coords = [(coord[0], coord[1]) for coord in segment_line.coords]  # Based on debug: geometry is (lat, lon)
                    
                    # Create popup text
                    popup_text = f"""
                    <b>{segment.name or 'Unnamed Road'}</b><br>
                    Segment ID: {segment.segment_id}<br>
                    Type: {segment.road_type or 'Unknown'}<br>
                    Length: {segment.length:.0f}m<br>
                    Status: {'✅ Run' if segment.has_been_run else '❌ Not Run'}
                    """
                    
                    # Determine which layer to add to based on run status
                    if segment.has_been_run:
                        color = 'green'
                        run_segments += 1
                        opacity = 0.8
                        weight = 4
                        target_layer = run_segments_layer
                    else:
                        color = 'red'
                        not_run_segments += 1
                        opacity = 0.6
                        weight = 3
                        target_layer = not_run_segments_layer
                    
                    # Add line to appropriate layer
                    folium.PolyLine(
                        locations=coords,
                        color=color,
                        weight=weight,
                        opacity=opacity,
                        popup=folium.Popup(popup_text, max_width=300)
                    ).add_to(target_layer)
                else:
                    error_segments += 1
                    
            except Exception as e:
                error_segments += 1
                print(f"  Warning: Error processing segment {segment.segment_id}: {str(e)}")
                continue
        
        # Add layer control to toggle different types on/off
        folium.LayerControl(collapsed=False).add_to(m)
        
        # Add legend
        legend_html = f'''
        <div style="position: fixed; 
                    bottom: 10px; left: 10px; width: 200px; height: 140px;
                    border:2px solid grey; z-index:9999; background-color:white;
                    padding: 8px; font-size: 12px;">
            <h5 style="margin-top:0; margin-bottom:5px">Running Progress</h5>
            <p style="margin:2px 0"><b>✅ Run: {run_segments}</b></p>
            <p style="margin:2px 0"><b>❌ Not Run: {not_run_segments}</b></p>
            <p style="margin:2px 0"><b>🗺️ GPS Tracks: {gps_tracks_added}</b></p>
            <p style="margin:2px 0"><b>⚠️ Errors: {error_segments}</b></p>
            <p style="margin:2px 0"><b>Progress: {(run_segments/(run_segments + not_run_segments)*100):.1f}%</b></p>
            <p style="font-size: 10px; margin:2px 0">Use layer control to toggle data on/off</p>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Add JavaScript for all on/off buttons in layer control
        all_toggle_js = '''
        <script>
        function addToggleButtons() {
            console.log('Attempting to add toggle buttons...');
            
            // Try multiple selectors to find the layer control
            var layerControl = document.querySelector('.leaflet-control-layers') || 
                              document.querySelector('.leaflet-control-layers-expanded') ||
                              document.querySelector('[class*="leaflet-control-layers"]');
            
            console.log('Layer control found:', layerControl);
            
            if (layerControl) {
                // Check if buttons already exist to avoid duplicates
                if (layerControl.querySelector('.toggle-buttons')) {
                    console.log('Buttons already exist');
                    return;
                }
                
                // Create buttons container
                var buttonDiv = document.createElement('div');
                buttonDiv.className = 'toggle-buttons';
                buttonDiv.style.cssText = 'margin: 8px 5px; text-align: center; border-top: 1px solid #ccc; padding-top: 5px;';
                
                // All On button
                var allOnBtn = document.createElement('button');
                allOnBtn.innerHTML = 'All On';
                allOnBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #4CAF50; color: white; border: none; border-radius: 3px;';
                allOnBtn.onclick = function() {
                    console.log('All On clicked');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Found checkboxes:', checkboxes.length);
                    checkboxes.forEach(function(cb) {
                        if (!cb.checked) {
                            console.log('Checking:', cb);
                            cb.click();
                        }
                    });
                };
                
                // All Off button
                var allOffBtn = document.createElement('button');
                allOffBtn.innerHTML = 'All Off';
                allOffBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #f44336; color: white; border: none; border-radius: 3px;';
                allOffBtn.onclick = function() {
                    console.log('All Off clicked');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Found checkboxes:', checkboxes.length);
                    checkboxes.forEach(function(cb) {
                        if (cb.checked) {
                            console.log('Unchecking:', cb);
                            cb.click();
                        }
                    });
                };
                
                // Debug button to check layer visibility
                var debugBtn = document.createElement('button');
                debugBtn.innerHTML = 'Debug';
                debugBtn.style.cssText = 'margin: 2px; font-size: 10px; padding: 3px 8px; cursor: pointer; background: #2196F3; color: white; border: none; border-radius: 3px;';
                debugBtn.onclick = function() {
                    console.log('=== LAYER DEBUG INFO ===');
                    var checkboxes = document.querySelectorAll('.leaflet-control-layers input[type="checkbox"]');
                    console.log('Total layer checkboxes found:', checkboxes.length);
                    
                    checkboxes.forEach(function(cb, index) {
                        var label = cb.parentElement.textContent.trim();
                        console.log(`Layer ${index + 1}: "${label}" - Checked: ${cb.checked}`);
                    });
                    
                    // Check for polylines on the map
                    var polylines = document.querySelectorAll('.leaflet-overlay-pane path');
                    console.log('Total polylines in DOM:', polylines.length);
                    
                    // Check for invisible polylines
                    var visiblePolylines = 0;
                    polylines.forEach(function(path) {
                        var style = window.getComputedStyle(path);
                        if (style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0') {
                            visiblePolylines++;
                        }
                    });
                    console.log('Visible polylines:', visiblePolylines);
                    console.log('=== END DEBUG INFO ===');
                };
                
                buttonDiv.appendChild(allOnBtn);
                buttonDiv.appendChild(allOffBtn);
                buttonDiv.appendChild(debugBtn);
                
                // Try multiple insertion points
                var insertPoint = layerControl.querySelector('form') || 
                                 layerControl.querySelector('.leaflet-control-layers-list') ||
                                 layerControl;
                
                insertPoint.appendChild(buttonDiv);
                console.log('Buttons added successfully');
                return true;
            }
            console.log('Layer control not found');
            return false;
        }
        
        // Try multiple times with increasing delays
        setTimeout(addToggleButtons, 500);
        setTimeout(addToggleButtons, 1500);
        setTimeout(addToggleButtons, 3000);
        
        // Also try when the page is fully loaded
        window.addEventListener('load', function() {
            setTimeout(addToggleButtons, 1000);
        });
        </script>
        '''
        m.get_root().html.add_child(folium.Element(all_toggle_js))
        
        # Save the map
        # Ensure visualizations folder exists
        ensure_output_folders()
        
        output_file = os.path.join('visualizations', 'user_progress_map.html')
        m.save(output_file)
        
        print(f"\n✓ Progress map created successfully!")
        print(f"📊 Visualization Statistics:")
        print(f"  ✅ Run segments: {run_segments}")
        print(f"  ❌ Not run segments: {not_run_segments}")
        print(f"  🗺️ GPS Tracks: {gps_tracks_added}")
        print(f"  ⚠️  Error segments: {error_segments}")
        print(f"  📈 Progress: {(run_segments/(run_segments + not_run_segments)*100):.1f}%" if (run_segments + not_run_segments) > 0 else "  📈 Progress: 0%")
        print(f"\n🗺️  Map saved as: {output_file}")
        print("   Open this file in your web browser to view the interactive map!")
        
    except Exception as e:
        print(f"\nError creating progress visualization: {str(e)}")
        import traceback
        traceback.print_exc()

def diagnose_road_segments(db, user):
    """Diagnose and fix road segment issues"""
    print("\n=== Diagnose and Fix Road Segment Issues ===")
    
    # Check user's basic setup
    print("1. Checking user setup...")
    if not user.locations:
        print("   ❌ No locations found. You need to add a location first (menu option 2).")
        return
    
    print(f"   ✅ Found {len(user.locations)} location(s):")
    for i, loc in enumerate(user.locations, 1):
        route_info = f" ({getattr(loc, 'route_count', 'Unknown')} routes)" if hasattr(loc, 'route_count') else ""
        print(f"      {i}. {loc.name} ({loc.address}){route_info}")
    
    # Check road segments in database
    print("\n2. Checking road segments in database...")
    from database.models import RoadSegment
    total_road_segments = db.query(RoadSegment).count()
    print(f"   Total road segments in database: {total_road_segments}")
    
    if total_road_segments == 0:
        print("   ❌ No road segments found in database!")
        print("   This means locations haven't been processed to generate road segments.")
        
        # Offer to process locations
        process = input("\n   Would you like to process your locations to generate road segments? (y/n): ")
        if process.lower() == 'y':
            for location in user.locations:
                print(f"\n   Processing location: {location.name}...")
                if process_location_routes(db, location):
                    print(f"   ✅ Successfully processed {location.name}")
                else:
                    print(f"   ❌ Failed to process {location.name}")
            
            # Recheck after processing
            total_road_segments = db.query(RoadSegment).count()
            print(f"\n   After processing: {total_road_segments} road segments in database")
        else:
            print("   Please process locations first using menu option 2 (Add new location)")
            return
    else:
        print(f"   ✅ Found {total_road_segments} road segments in database")
    
    # Check user road segments
    print("\n3. Checking user road segments...")
    user_segments = get_user_road_segments(db, user.id)
    print(f"   User road segments: {len(user_segments)}")
    
    if len(user_segments) == 0:
        print("   ❌ No user road segments found!")
        print("   Attempting to sync user road segments...")
        
        try:
            sync_user_road_segments(db, user.id)
            user_segments = get_user_road_segments(db, user.id)
            print(f"   After sync: {len(user_segments)} user road segments")
            
            if len(user_segments) == 0:
                print("   ❌ Still no user road segments after sync!")
                print("   This might indicate a data issue. Try re-processing locations.")
            else:
                print("   ✅ Successfully synced user road segments")
        except Exception as e:
            print(f"   ❌ Error during sync: {str(e)}")
            return
    else:
        print(f"   ✅ Found {len(user_segments)} user road segments")
    
    # Check geometry data
    if user_segments:
        print("\n4. Checking geometry data...")
        segments_with_geometry = sum(1 for s in user_segments if s.geometry)
        segments_without_geometry = len(user_segments) - segments_with_geometry
        
        print(f"   Segments with geometry: {segments_with_geometry}")
        print(f"   Segments without geometry: {segments_without_geometry}")
        
        if segments_without_geometry > 0:
            print("   ⚠️  Some segments are missing geometry data!")
            print("   This will prevent proper visualization and GPS analysis.")
            
            # Show a sample of segments without geometry
            missing_geo = [s for s in user_segments if not s.geometry][:5]
            print("   Sample segments missing geometry:")
            for seg in missing_geo:
                print(f"      - {seg.osm_id}: {seg.name}")
        else:
            print("   ✅ All user road segments have geometry data")
    
    # Check activities
    print("\n5. Checking GPS activities...")
    activities = get_user_activities(db, user.id)
    print(f"   Total activities: {len(activities)}")
    
    if len(activities) == 0:
        print("   ❌ No GPS activities found!")
        print("   Load some GPS data first using menu option 5")
    else:
        print(f"   ✅ Found {len(activities)} activities")
        
        # Check GPS points
        total_gps_points = 0
        for activity in activities:
            gps_points = get_activity_gps_points(db, activity.id)
            total_gps_points += len(gps_points)
        
        print(f"   Total GPS points: {total_gps_points}")
        
        if total_gps_points == 0:
            print("   ⚠️  Activities exist but have no GPS points!")
        else:
            print(f"   ✅ GPS data looks good")
    
    # Check run status
    if user_segments:
        print("\n6. Checking run status...")
        segments_run = sum(1 for s in user_segments if s.has_been_run)
        segments_not_run = len(user_segments) - segments_run
        
        print(f"   Segments marked as run: {segments_run}")
        print(f"   Segments not run: {segments_not_run}")
        
        if segments_run == 0 and len(activities) > 0:
            print("   ⚠️  You have GPS activities but no segments are marked as run!")
            print("   Try running GPS analysis (menu option 6)")
    
    # Final recommendations
    print("\n=== Recommendations ===")
    
    if len(user_segments) == 0:
        print("❌ Priority: Fix user road segments issue")
        print("   Try: Re-add your locations or process existing ones")
    elif segments_with_geometry < len(user_segments):
        print("⚠️  Priority: Fix missing geometry data")
        print("   Try: Re-process your locations")
    elif len(activities) == 0:
        print("📍 Next step: Load GPS data")
        print("   Use: Menu option 5 to load Strava files")
    elif total_gps_points == 0:
        print("⚠️  Priority: Fix GPS data issues")
        print("   Check: Your Strava files contain valid GPS points")
    elif segments_run == 0:
        print("🔍 Next step: Analyze GPS data")
        print("   Use: Menu option 6 to match GPS to road segments")
    else:
        print("✅ Everything looks good!")
        print("   Use: Menu option 7 to visualize your progress")
    
    # Check for broken route-segment relationships
    print("\n=== Route Visualization Issues ===")
    if user.locations:
        location = user.locations[0]  # Check first location
        routes = db.query(Route).filter(Route.location_id == location.id).all()
        
        if routes:
            routes_with_no_segments = []
            for route in routes:
                segment_count = (
                    db.query(route_segments)
                    .filter(route_segments.c.route_id == route.id)
                    .count()
                )
                if segment_count == 0:
                    routes_with_no_segments.append(route)
            
            if routes_with_no_segments:
                print(f"⚠️  Found {len(routes_with_no_segments)} routes with broken segment relationships")
                print("   This explains why some routes don't visualize")
                
                fix_choice = input(f"\n   Fix broken route relationships for {location.name}? (y/n): ")
                if fix_choice.lower() == 'y':
                    if fix_broken_route_segments(db, location.id):
                        print("✅ Route relationships fixed!")
                    else:
                        print("❌ Failed to fix route relationships")
            else:
                print("✅ All routes have proper segment relationships")
        else:
            print("ℹ️  No routes found to check")

def visualize_all_road_segments(db):
    """Visualize all road segments in the database"""
    print("\n=== Visualize All Road Segments ===")
    
    try:
        # Get all road segments from database
        from database.models import RoadSegment
        all_segments = db.query(RoadSegment).all()
        
        if not all_segments:
            print("No road segments found in database.")
            return
        
        print(f"Found {len(all_segments)} road segments in database")
        
        # Check geometry data
        segments_with_geometry = sum(1 for s in all_segments if s.geometry)
        segments_without_geometry = len(all_segments) - segments_with_geometry
        
        print(f"Segments with geometry: {segments_with_geometry}")
        print(f"Segments without geometry: {segments_without_geometry}")
        
        if segments_with_geometry == 0:
            print("❌ No segments have geometry data. Cannot create visualization.")
            return
        
        # Create visualization using Folium
        from geoalchemy2.shape import to_shape
        import folium
        import numpy as np
        
        print("Creating road segments visualization...")
        
        # Calculate map center from all segments
        all_coords = []
        
        for segment in all_segments:
            if segment.geometry:
                try:
                    segment_line = to_shape(segment.geometry)
                    if hasattr(segment_line, 'coords'):
                        for coord in segment_line.coords:
                            all_coords.append((coord[0], coord[1]))  # Based on debug: geometry is (lat, lon)
                except Exception as e:
                    print(f"  Warning: Error processing geometry for segment {segment.segment_id}: {e}")
                    continue
        
        if not all_coords:
            print("❌ No valid segment geometries found.")
            return
        
        # Calculate center point
        coords_array = np.array(all_coords)
        center_lat = np.mean(coords_array[:, 0])
        center_lon = np.mean(coords_array[:, 1])
        
        print(f"Creating map centered at ({center_lat:.4f}, {center_lon:.4f})")
        
        # Create map
        m = folium.Map(
            location=[center_lat, center_lon], 
            zoom_start=13,
            tiles='OpenStreetMap'
        )
        
        # Group segments by road type for better organization
        road_type_colors = {
            'primary': 'red',
            'secondary': 'orange', 
            'tertiary': 'yellow',
            'residential': 'green',
            'service': 'blue',
            'footway': 'purple',
            'path': 'brown',
            'cycleway': 'pink',
            'trunk': 'darkred',
            'motorway': 'black',
            'unclassified': 'gray'
        }
        
        # Create feature groups by road type
        road_type_groups = {}
        segments_visualized = 0
        
        for segment in all_segments:
            if not segment.geometry:
                continue
                
            try:
                segment_line = to_shape(segment.geometry)
                
                if hasattr(segment_line, 'coords'):
                    coords = [(coord[0], coord[1]) for coord in segment_line.coords]  # (lat, lon)
                    
                    # Determine road type and color
                    road_type = segment.road_type or 'unclassified'
                    color = road_type_colors.get(road_type, 'gray')
                    
                    # Create or get feature group for this road type
                    if road_type not in road_type_groups:
                        road_type_groups[road_type] = folium.FeatureGroup(name=f"{road_type.title()} Roads")
                        road_type_groups[road_type].add_to(m)
                    
                    # Create popup text
                    popup_text = f"""
                    <b>{segment.name or 'Unnamed Road'}</b><br>
                    Segment ID: {segment.segment_id}<br>
                    OSM ID: {segment.osm_id}<br>
                    Type: {road_type}<br>
                    Length: {segment.length:.0f}m
                    """
                    
                    # Add line to the appropriate road type group
                    folium.PolyLine(
                        locations=coords,
                        color=color,
                        weight=2,
                        opacity=0.7,
                        popup=folium.Popup(popup_text, max_width=300)
                    ).add_to(road_type_groups[road_type])
                    
                    segments_visualized += 1
                    
            except Exception as e:
                print(f"    Warning: Error processing segment {segment.segment_id}: {str(e)}")
                continue
        
        # Add layer control to toggle road types on/off
        folium.LayerControl(collapsed=False).add_to(m)
        
        # Add legend
        legend_html = f'''
        <div style="position: fixed; 
                    bottom: 10px; left: 10px; width: 200px; height: 140px;
                    border:2px solid grey; z-index:9999; background-color:white;
                    padding: 8px; font-size: 12px;">
            <h5 style="margin-top:0; margin-bottom:5px">All Road Segments</h5>
            <p style="margin:2px 0"><b>Total: {len(all_segments)} segments</b></p>
            <p style="margin:2px 0"><b>Visualized: {segments_visualized}</b></p>
            <p style="margin:2px 0"><b>Road types: {len(road_type_groups)}</b></p>
            <p style="font-size: 10px; margin:2px 0">Use layer control to filter by road type</p>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Save the map
        # Ensure visualizations folder exists
        ensure_output_folders()
        
        output_file = os.path.join('visualizations', 'all_road_segments.html')
        m.save(output_file)
        
        print(f"\n✓ Road segments visualization created successfully!")
        print(f"📊 Visualization Statistics:")
        print(f"  🗺️  Total segments in DB: {len(all_segments)}")
        print(f"  📍 Segments visualized: {segments_visualized}")
        print(f"  🛣️  Road types found: {len(road_type_groups)}")
        print(f"  📂 Road types: {', '.join(sorted(road_type_groups.keys()))}")
        print(f"\n🗺️  Map saved as: {output_file}")
        print("   Open this file in your web browser to view all road segments!")
        print("   You can toggle different road types on/off using the layer control.")
        
    except Exception as e:
        print(f"\nError creating road segments visualization: {str(e)}")
        import traceback
        traceback.print_exc()

def fix_broken_route_segments(db, location_id):
    """
    Fix routes that exist but have no segments in the route_segments table
    This can happen due to transaction issues during route creation
    """
    print("\n=== Fixing Broken Route-Segment Relationships ===")
    
    # Find routes with no segments
    routes = db.query(Route).filter(Route.location_id == location_id).all()
    
    routes_fixed = 0
    routes_failed = 0
    
    for route in routes:
        segment_count = (
            db.query(route_segments)
            .filter(route_segments.c.route_id == route.id)
            .count()
        )
        
        if segment_count == 0:
            print(f"\n🔧 Attempting to fix Route {route.id}: {route.name}")
            
            # Check if this route has any data we can recover
            # Look for similar routes or try to regenerate
            print(f"   Route has {getattr(route, 'node_count', 'unknown')} nodes")
            print(f"   Route distance: {getattr(route, 'distance', 'unknown')}m")
            
            # For now, mark these routes for deletion since they're broken
            print(f"   ❌ Route {route.id} cannot be recovered - marking for deletion")
            routes_failed += 1
    
    if routes_failed > 0:
        print(f"\n⚠️  Found {routes_failed} broken routes that cannot be fixed.")
        print("   Recommendation: Delete these routes and re-process the location")
        
        delete_broken = input(f"\n   Delete {routes_failed} broken routes? (y/n): ")
        if delete_broken.lower() == 'y':
            for route in routes:
                segment_count = (
                    db.query(route_segments)
                    .filter(route_segments.c.route_id == route.id)
                    .count()
                )
                
                if segment_count == 0:
                    print(f"   Deleting broken route {route.id}: {route.name}")
                    db.delete(route)
                    routes_fixed += 1
            
            try:
                db.commit()
                print(f"\n✅ Successfully deleted {routes_fixed} broken routes")
                
                # Update location route count
                location = db.query(Location).filter(Location.id == location_id).first()
                if location:
                    remaining_routes = db.query(Route).filter(Route.location_id == location_id).count()
                    location.route_count = remaining_routes
                    db.commit()
                    print(f"   Updated location route count to {remaining_routes}")
                
            except Exception as e:
                print(f"   ❌ Error deleting routes: {str(e)}")
                db.rollback()
                return False
        else:
            print("   Deletion cancelled.")
    
    return routes_fixed > 0

def visualize_specific_route(db, user):
    """Visualize a specific route with detailed debugging"""
    print("\n=== Visualize Specific Route ===")
    
    # First, let user select a location
    location = select_location(db, user)
    if not location:
        return
    
    # Get all routes for this location
    routes = db.query(Route).filter(Route.location_id == location.id).all()
    
    if not routes:
        print(f"No routes found for location: {location.name}")
        return
    
    print(f"\nFound {len(routes)} routes for {location.name}:")
    for i, route in enumerate(routes, 1):
        segment_count = (
            db.query(route_segments)
            .filter(route_segments.c.route_id == route.id)
            .count()
        )
        distance_info = f" - {route.distance/1000:.1f}km" if hasattr(route, 'distance') and route.distance else ""
        print(f"{i}. Route {route.id}: {route.name} ({segment_count} segments){distance_info}")
    
    # Let user select a specific route
    while True:
        choice = input(f"\nSelect route number to visualize (1-{len(routes)}, or 0 to go back): ")
        if choice == "0":
            return
        
        try:
            index = int(choice) - 1
            if 0 <= index < len(routes):
                selected_route = routes[index]
                break
        except ValueError:
            pass
        
        print("Invalid selection. Please try again.")
    
    print(f"\n🔍 DETAILED ROUTE ANALYSIS: Route {selected_route.id}")
    print(f"Route Name: {selected_route.name}")
    
    try:
        # Get route segments with detailed info
        segments = (
            db.query(RoadSegment, route_segments.c.direction, route_segments.c.segment_order)
            .join(route_segments)
            .filter(route_segments.c.route_id == selected_route.id)
            .order_by(route_segments.c.segment_order)
            .all()
        )
        
        print(f"Total segments in route: {len(segments)}")
        
        if not segments:
            print("❌ No segments found for this route!")
            return
        
        # Analyze each segment
        from geoalchemy2.shape import to_shape
        import folium
        import numpy as np
        
        print("\n📊 SEGMENT ANALYSIS:")
        segments_with_geometry = 0
        segments_without_geometry = 0
        invalid_coordinates = 0
        total_length = 0
        coordinate_bounds = {'min_lat': float('inf'), 'max_lat': float('-inf'), 
                           'min_lon': float('inf'), 'max_lon': float('-inf')}
        
        for i, (segment, direction, order) in enumerate(segments):
            print(f"\nSegment {i+1} (Order {order}):")
            print(f"  Segment ID: {segment.segment_id}")
            print(f"  Name: {segment.name or 'Unnamed'}")
            print(f"  Type: {segment.road_type or 'Unknown'}")
            print(f"  Length: {segment.length:.1f}m")
            print(f"  Direction: {'Forward' if direction else 'Reverse'}")
            print(f"  Has geometry: {segment.geometry is not None}")
            
            if segment.geometry:
                segments_with_geometry += 1
                try:
                    segment_line = to_shape(segment.geometry)
                    
                    if hasattr(segment_line, 'coords'):
                        coords_list = list(segment_line.coords)
                        print(f"  Coordinates: {len(coords_list)} points")
                        
                        if coords_list:
                            first_coord = coords_list[0]
                            last_coord = coords_list[-1]
                            print(f"  Start: ({first_coord[0]:.6f}, {first_coord[1]:.6f})")
                            print(f"  End: ({last_coord[0]:.6f}, {last_coord[1]:.6f})")
                            
                            # Track coordinate bounds
                            for coord in coords_list:
                                lat, lon = coord[0], coord[1]
                                if -90 <= lat <= 90 and -180 <= lon <= 180:
                                    coordinate_bounds['min_lat'] = min(coordinate_bounds['min_lat'], lat)
                                    coordinate_bounds['max_lat'] = max(coordinate_bounds['max_lat'], lat)
                                    coordinate_bounds['min_lon'] = min(coordinate_bounds['min_lon'], lon)
                                    coordinate_bounds['max_lon'] = max(coordinate_bounds['max_lon'], lon)
                                else:
                                    invalid_coordinates += 1
                                    print(f"  ❌ INVALID COORD: ({lat}, {lon})")
                            
                            total_length += segment.length
                        else:
                            print(f"  ❌ NO COORDINATES in geometry")
                            segments_without_geometry += 1
                    else:
                        print(f"  ❌ GEOMETRY has no coords attribute")
                        segments_without_geometry += 1
                        
                except Exception as e:
                    print(f"  ❌ ERROR processing geometry: {str(e)}")
                    segments_without_geometry += 1
            else:
                segments_without_geometry += 1
        
        print(f"\n📈 ROUTE SUMMARY:")
        print(f"  Segments with geometry: {segments_with_geometry}")
        print(f"  Segments without geometry: {segments_without_geometry}")
        print(f"  Invalid coordinates found: {invalid_coordinates}")
        print(f"  Total route length: {total_length/1000:.2f}km")
        
        if coordinate_bounds['min_lat'] != float('inf'):
            print(f"  Coordinate bounds:")
            print(f"    Latitude: {coordinate_bounds['min_lat']:.6f} to {coordinate_bounds['max_lat']:.6f}")
            print(f"    Longitude: {coordinate_bounds['min_lon']:.6f} to {coordinate_bounds['max_lon']:.6f}")
            
            # Calculate center point
            center_lat = (coordinate_bounds['min_lat'] + coordinate_bounds['max_lat']) / 2
            center_lon = (coordinate_bounds['min_lon'] + coordinate_bounds['max_lon']) / 2
            print(f"    Center: ({center_lat:.6f}, {center_lon:.6f})")
        
        if segments_with_geometry == 0:
            print("❌ Cannot create visualization - no segments have geometry")
            return
        
        # Create single-route visualization
        print(f"\n🗺️  Creating visualization for Route {selected_route.id}...")
        
        # Use coordinate bounds to center map
        if coordinate_bounds['min_lat'] != float('inf'):
            map_center = [center_lat, center_lon]
        else:
            map_center = [location.latitude, location.longitude]
        
        m = folium.Map(
            location=map_center,
            zoom_start=15,
            tiles='OpenStreetMap'
        )
        
        # Add route segments
        segments_rendered = 0
        segments_skipped = 0
        
        for i, (segment, direction, order) in enumerate(segments):
            if not segment.geometry:
                segments_skipped += 1
                continue
                
            try:
                segment_line = to_shape(segment.geometry)
                
                if hasattr(segment_line, 'coords'):
                    coords = [(coord[0], coord[1]) for coord in segment_line.coords]
                    
                    # Create detailed popup
                    popup_text = f"""
                    <b>Route {selected_route.id} - Segment {order}</b><br>
                    <b>{segment.name or 'Unnamed Road'}</b><br>
                    Segment ID: {segment.segment_id}<br>
                    OSM ID: {segment.osm_id}<br>
                    Type: {segment.road_type or 'Unknown'}<br>
                    Length: {segment.length:.0f}m<br>
                    Direction: {'Forward' if direction else 'Reverse'}<br>
                    Coordinates: {len(coords)} points<br>
                    Order in route: {order}
                    """
                    
                    # Color segments differently based on order for debugging
                    if i < len(segments) * 0.33:
                        color = '#FF0000'  # Bright red
                    elif i < len(segments) * 0.66:
                        color = '#FF8000'  # Bright orange
                    else:
                        color = '#00FF00'  # Bright green
                    
                    folium.PolyLine(
                        locations=coords,
                        color=color,
                        weight=6,
                        opacity=0.8,
                        popup=folium.Popup(popup_text, max_width=400)
                    ).add_to(m)
                    
                    segments_rendered += 1
                    
            except Exception as e:
                print(f"  Warning: Error rendering segment {segment.segment_id}: {str(e)}")
                segments_skipped += 1
                continue
        
        # Add start and end markers
        if segments and segments[0][0].geometry:
            try:
                first_segment_line = to_shape(segments[0][0].geometry)
                if hasattr(first_segment_line, 'coords'):
                    first_coords = list(first_segment_line.coords)
                    if first_coords:
                        start_coord = first_coords[0]
                        folium.Marker(
                            location=[start_coord[0], start_coord[1]],
                            popup="Route Start",
                            icon=folium.Icon(color='green', icon='play')
                        ).add_to(m)
            except:
                pass
        
        if segments and segments[-1][0].geometry:
            try:
                last_segment_line = to_shape(segments[-1][0].geometry)
                if hasattr(last_segment_line, 'coords'):
                    last_coords = list(last_segment_line.coords)
                    if last_coords:
                        end_coord = last_coords[-1]
                        folium.Marker(
                            location=[end_coord[0], end_coord[1]],
                            popup="Route End",
                            icon=folium.Icon(color='red', icon='stop')
                        ).add_to(m)
            except:
                pass
        
        # Add legend
        legend_html = f'''
        <div style="position: fixed; 
                    bottom: 10px; left: 10px; width: 250px; height: 180px;
                    border:2px solid grey; z-index:9999; background-color:white;
                    padding: 8px; font-size: 12px;">
            <h5 style="margin-top:0; margin-bottom:5px">Route {selected_route.id} Debug</h5>
            <p style="margin:2px 0"><b>Name:</b> {selected_route.name}</p>
            <p style="margin:2px 0"><b>Total segments:</b> {len(segments)}</p>
            <p style="margin:2px 0"><b>Rendered:</b> {segments_rendered}</p>
            <p style="margin:2px 0"><b>Skipped:</b> {segments_skipped}</p>
            <p style="margin:2px 0"><b>Length:</b> {total_length/1000:.2f}km</p>
            <p style="margin:2px 0; color: #FF0000"><b>Red:</b> Start segments</p>
            <p style="margin:2px 0; color: #FF8000"><b>Orange:</b> Middle segments</p>
            <p style="margin:2px 0; color: #00FF00"><b>Green:</b> End segments</p>
        </div>
        '''
        m.get_root().html.add_child(folium.Element(legend_html))
        
        # Save the map
        # Ensure visualizations folder exists
        ensure_output_folders()
        
        output_file = os.path.join('visualizations', f'route_{selected_route.id}_debug.html')
        m.save(output_file)
        
        print(f"\n✅ Route visualization created!")
        print(f"📊 Rendering Results:")
        print(f"  Segments rendered: {segments_rendered}/{len(segments)}")
        print(f"  Segments skipped: {segments_skipped}")
        print(f"🗺️  Map saved as: {output_file}")
        print("   Open this file to see the detailed route visualization")
        print("   Segments are color-coded by position: Red (start) → Orange (middle) → Green (end)")
        
    except Exception as e:
        print(f"\nError creating route visualization: {str(e)}")
        import traceback
        traceback.print_exc()

def cleanup_existing_files():
    """Move existing files to appropriate folders"""
    print("\n=== Cleaning Up Files ===")
    
    # Create folders if they don't exist
    ensure_output_folders()
    
    import shutil
    
    # HTML files to move to visualizations
    html_files = [
        'route_map.html',
        'user_progress_map.html', 
        'all_road_segments.html',
        'initial_solution.html'
    ]
    
    files_moved = 0
    
    # Move HTML files
    for file in html_files:
        if os.path.exists(file):
            dest = os.path.join('visualizations', file)
            try:
                shutil.move(file, dest)
                print(f'✅ Moved {file} to visualizations/')
                files_moved += 1
            except Exception as e:
                print(f'❌ Error moving {file}: {str(e)}')
    
    # Move any route debug HTML files
    for file in os.listdir('.'):
        if file.startswith('route_') and file.endswith('_debug.html'):
            dest = os.path.join('visualizations', file)
            try:
                shutil.move(file, dest)
                print(f'✅ Moved {file} to visualizations/')
                files_moved += 1
            except Exception as e:
                print(f'❌ Error moving {file}: {str(e)}')
    
    # CSV files to move to debug
    for file in os.listdir('.'):
        if file.endswith('.csv') and (file.startswith('cycles_') or file.startswith('routes_debug_')):
            dest = os.path.join('debug', file)
            try:
                shutil.move(file, dest)
                print(f'✅ Moved {file} to debug/')
                files_moved += 1
            except Exception as e:
                print(f'❌ Error moving {file}: {str(e)}')
    
    print(f'\n✅ File cleanup complete! Moved {files_moved} files.')
    print(f'📁 HTML files are now in: visualizations/')
    print(f'📁 CSV files are now in: debug/')
    
    return files_moved > 0

def main():
    # Get user information
    username = input("Enter username (or new username to register): ")
    
    # Try to get existing user or register new one
    db = SessionLocal()
    try:
        user = get_user_by_username(db, username)
        
        if not user:
            print(f"\nUsername '{username}' not found. Registering new user...")
            user = register_user(db, username)
            if not user:
                print("Failed to register user. Exiting.")
                return
            
            print("\nWelcome! Let's set up your first location.")
            add_new_location(db, user)
        
        # Main application loop
        while True:
            choice = display_menu(username)
            
            if choice == "0":
                confirm = input("\nWARNING: This will permanently delete all data in the database. Are you sure? (type 'yes' to confirm): ")
                if confirm.lower() == 'yes':
                    print("\nClearing database...")
                    if clear_database(db):
                        print("Database cleared successfully. Please restart the application.")
                        break
                    else:
                        print("Failed to clear database.")
                else:
                    print("\nDatabase reset cancelled.")
                
            elif choice == "00":
                print("\nGoodbye!")
                break
                
            elif choice == "1":
                location = handle_locations(db, user)
                if location:
                    print(f"\nLocation processing complete: {location.name}")
                    # Refresh user to get updated data
                    db.refresh(user)
                
            elif choice == "2":
                add_new_location(db, user)
                
            elif choice == "3":
                remove_user_location(db, user)
                
            elif choice == "4":
                location = select_location(db, user)
                if location:
                    visualize_location_data(db, location)
                
            elif choice == "5":
                handle_load_strava_gps_data(db, user)
                
            elif choice == "6":
                handle_analyze_gps_data(db, user)
                
            elif choice == "7":
                visualize_user_progress(db, user)
                
            elif choice == "8":
                diagnose_road_segments(db, user)
                
            elif choice == "9":
                visualize_all_road_segments(db)
                
            elif choice == "10":
                visualize_specific_route(db, user)
                
            elif choice == "11":
                cleanup_existing_files()
                
            else:
                print("\nInvalid option. Please try again.")
            
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main() 