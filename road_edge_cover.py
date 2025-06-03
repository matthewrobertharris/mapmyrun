import logging
import osmnx as ox
import folium
from visualization import visualize_solution, visualize_road_network, visualize_classification
from metrics import print_metrics
from graph_processing import (
    get_coordinates,
    get_road_network,
    calculate_edge_cover,
    calculate_solution_metrics,
    analyze_excluded_edges
)
from strava_analysis import classify_road_segments
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
    clear_database
)
from database.models import User, RoadSegment, Route, route_segments
from datetime import datetime
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

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
    print("1. View my locations")
    print("2. Add new location")
    print("3. Remove location")
    print("4. Start route planning")
    print("5. View my statistics")
    print("6. Visualize location data")
    print("9. Reset database (WARNING: Deletes all data)")
    print("0. Exit")
    return input("\nSelect an option: ")

def handle_locations(db, user):
    """Display and manage user locations"""
    if not user.locations:
        print("\nYou don't have any locations yet.")
        return None
        
    print("\n=== Your Locations ===")
    for i, loc in enumerate(user.locations, 1):
        print(f"{i}. {loc.name} ({loc.address})")
    
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
            osm_id = str(data.get('osmid', f"{u}-{v}"))
            name = str(data.get('name', ''))
            road_type = str(data.get('highway', ''))
            length = float(data.get('length', 0))
            
            # Check if this is a bidirectional road we've already processed
            # OSM IDs are the same for both directions of the same road
            if osm_id in processed_osm_ids:
                bidirectional_count += 1
                continue
                
            processed_osm_ids.add(osm_id)
            
            # Check if segment already exists
            existing_segment = (
                db.query(RoadSegment)
                .filter(RoadSegment.osm_id == osm_id)
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
                # Create new segment
                segment = add_road_segment(
                    db,
                    osm_id,
                    name,
                    road_type,
                    coords,
                    length
                )
                if segment:
                    segments_added += 1
                else:
                    print(f"Warning: Failed to add segment {osm_id} (no error thrown)")
                    error_count += 1
                    
        except Exception as e:
            print(f"Error processing edge {u}->{v}: {str(e)}")
            error_count += 1
            continue
    
    try:
        db.commit()
        print("\nRoad segment processing summary:")
        print(f"Total edges in graph: {total_edges}")
        print(f"Bidirectional edges skipped: {bidirectional_count}")
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
    distance = location.max_distance  # Distance for road network retrieval
    max_distance = location.max_distance * 2.1  # Double the max distance to account for out and back, plus a little wiggle room
    
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
        
        # Visualize the initial solution before storing routes
        print("\nVisualizing initial solution...")
        metrics = calculate_solution_metrics(G, cycles, start_node, max_distance)
        visualize_solution(G, cycles, center_point, metrics, output_file='initial_solution.html')
        print("Initial solution saved to 'initial_solution.html'")
        
        # Store each cycle as a route
        print("\nStoring routes...")
        routes_created = 0
        
        for i, cycle in enumerate(cycles, 1):
            # Get the road segments for this cycle
            segment_osm_ids = []
            segment_directions = []
            used_combinations = set()  # Track which segments we've already used in this route
            
            for u, v in zip(cycle[:-1], cycle[1:]):
                # Get edge data
                edge_data = G.edges[u, v, 0]
                osm_id = str(edge_data.get('osmid', f"{u}-{v}"))
                
                # Find the corresponding road segment
                segment = (
                    db.query(RoadSegment)
                    .filter(RoadSegment.osm_id == osm_id)
                    .first()
                )
                
                if segment:
                    # Skip if we've already used this segment in this route
                    if segment.osm_id in used_combinations:
                        continue
                        
                    segment_osm_ids.append(segment.osm_id)
                    # Determine direction based on node order
                    # True means forward (u->v matches segment direction)
                    segment_directions.append(True)
                    used_combinations.add(segment.osm_id)
            
            if segment_osm_ids:
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
                        segment_osm_ids,
                        segment_directions
                    )
                    
                    if route:
                        routes_created += 1
                        db.commit()  # Commit after each successful route creation
                except Exception as e:
                    print(f"Error creating route {i}: {str(e)}")
                    db.rollback()  # Rollback on error
                    continue
        
        print(f"Successfully created {routes_created} routes!")
        
        # Sync user road segments
        print("Syncing user road segments...")
        sync_user_road_segments(db, location.user_id)
        
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
    
    center_point = (location.latitude, location.longitude)
    distance = 2000  # Distance for road network retrieval
    max_distance = location.max_distance * 2  # Double the max distance to account for out and back
    
    try:
        # Get road network
        print("Retrieving road network...")
        G = get_road_network(center_point, distance)
        
        # Get all routes for this location
        routes = db.query(Route).filter(Route.location_id == location.id).all()
        
        if not routes:
            print("No routes found for this location.")
            print("Displaying basic road network...")
            visualize_road_network(G, output_file='road_network.html')
            print("\nVisualization saved to 'road_network.html'")
            return
        
        # Track all segments for debugging
        all_graph_edges = set()
        all_route_segments = set()
        all_visualized_edges = set()
        
        # Get all edges from graph
        for u, v, data in G.edges(data=True):
            osm_id = str(data.get('osmid', f"{u}-{v}"))
            all_graph_edges.add(osm_id)
        
        print(f"\nTotal edges in graph: {len(all_graph_edges)}")
        
        # Get all segments from routes
        for route in routes:
            segments = (
                db.query(RoadSegment)
                .join(route_segments)
                .filter(route_segments.c.route_id == route.id)
                .all()
            )
            for segment in segments:
                all_route_segments.add(segment.osm_id)
        
        print(f"Total segments in routes: {len(all_route_segments)}")
        
        # Convert routes to cycles format for visualization
        print("Processing routes...")
        cycles = []
        metrics = {
            'total_distance': 0,
            'lower_bound': 0,
            'upper_bound': 0,
            'num_cycles': len(routes),
            'edges_covered': 0,
            'total_edges': len(G.edges()),
            'efficiency_vs_lower': 0,
            'efficiency_vs_upper': 0,
            'cycle_metrics': [],
            'coverage_stats': {
                'max_appearances': 0,
                'avg_appearances': 0
            }
        }
        
        # Track edge coverage
        edge_appearances = defaultdict(int)
        covered_edges = set()
        
        # Process each route
        for route in routes:
            # Get segments in order
            segments = (
                db.query(RoadSegment, route_segments.c.direction)
                .join(route_segments)
                .filter(route_segments.c.route_id == route.id)
                .order_by(route_segments.c.segment_order)
                .all()
            )
            
            if not segments:
                print(f"Warning: Route {route.id} has no segments")
                continue
                
            # Convert segments to cycle format (list of node IDs)
            cycle = []
            cycle_length = 0
            cycle_segments = set()  # Track segments in this cycle
            
            for segment, direction in segments:
                # Find the corresponding edge in the graph
                edge_found = False
                for u, v, data in G.edges(data=True):
                    if str(data.get('osmid', f"{u}-{v}")) == segment.osm_id:
                        edge_found = True
                        if direction:  # Forward direction
                            cycle.extend([u] if not cycle else [u, v])
                        else:  # Reverse direction
                            cycle.extend([v] if not cycle else [v, u])
                        cycle_length += data.get('length', 0)
                        edge = tuple(sorted([u, v]))
                        edge_appearances[edge] += 1
                        covered_edges.add(edge)
                        cycle_segments.add(segment.osm_id)
                        all_visualized_edges.add(segment.osm_id)
                        break
                
                if not edge_found:
                    print(f"Warning: Could not find edge in graph for segment {segment.osm_id} ({segment.name})")
            
            if cycle:
                print(f"Route {route.id}: Found {len(cycle_segments)} segments out of {len(segments)} in graph")
                cycles.append(cycle)
                metrics['total_distance'] += cycle_length
                metrics['cycle_metrics'].append({
                    'cycle_number': len(cycles),
                    'utilization': 1.0,  # Placeholder
                    'efficiency': 1.0  # Placeholder
                })
        
        # Print detailed segment analysis
        print("\nSegment Analysis:")
        print(f"Total edges in graph: {len(all_graph_edges)}")
        print(f"Total segments in routes: {len(all_route_segments)}")
        print(f"Total segments visualized: {len(all_visualized_edges)}")
        
        # Find segments in routes but not visualized
        missing_segments = all_route_segments - all_visualized_edges
        if missing_segments:
            print("\nSegments in routes but not visualized:")
            for osm_id in missing_segments:
                segment = db.query(RoadSegment).filter(RoadSegment.osm_id == osm_id).first()
                print(f"- {osm_id}: {segment.name} ({segment.road_type})")
        
        # Find edges in graph but not in routes
        unused_edges = all_graph_edges - all_route_segments
        if unused_edges:
            print(f"\nEdges in graph but not in routes: {len(unused_edges)}")
            print("Sample of unused edges:")
            for osm_id in list(unused_edges)[:5]:
                for u, v, data in G.edges(data=True):
                    if str(data.get('osmid', f"{u}-{v}")) == osm_id:
                        print(f"- {osm_id}: {data.get('name', 'Unnamed')} ({data.get('highway', 'unknown type')})")
                        break
        
        # Find nearest node to start point for excluded edge analysis
        start_node = ox.nearest_nodes(G, center_point[1], center_point[0])
        
        # Analyze excluded edges
        print("\nAnalyzing excluded edges...")
        excluded_edges = set()
        for u, v, data in G.edges(data=True):
            edge = tuple(sorted([u, v]))
            if edge not in covered_edges:
                excluded_edges.add(edge)
        
        print(f"Found {len(excluded_edges)} excluded edges out of {len(G.edges())} total edges")
        
        # Analyze why edges were excluded
        excluded_metrics = analyze_excluded_edges(G, start_node, covered_edges, max_distance)
        
        # Print analysis
        print("\nExcluded Edge Analysis:")
        print(f"Total excluded edges: {len(excluded_edges)}")
        
        reasons = defaultdict(int)
        for metric in excluded_metrics:
            reasons[metric['reason']] += 1
        
        print("\nExclusion reasons:")
        for reason, count in reasons.items():
            print(f"- {reason}: {count} edges")
        
        # Create visualization
        print("\nGenerating visualization...")
        visualize_solution(G, cycles, center_point, metrics, output_file='route_map.html')
        print("\nVisualization saved to 'route_map.html'")
        
    except Exception as e:
        print(f"Error during visualization: {str(e)}")
        import traceback
        traceback.print_exc()

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
                print("\nGoodbye!")
                break
                
            elif choice == "1":
                location = handle_locations(db, user)
                if location:
                    print(f"\nSelected: {location.name} ({location.address})")
                    
            elif choice == "2":
                add_new_location(db, user)
                
            elif choice == "3":
                remove_user_location(db, user)
                
            elif choice == "4":
                location = handle_locations(db, user)
                if location:
                    print("\nRoute planning feature coming soon!")
                    # This is where we'll add back the route planning logic
                    
            elif choice == "5":
                print("\nStatistics feature coming soon!")
                # This is where we'll add user statistics
                
            elif choice == "6":
                location = handle_locations(db, user)
                if location:
                    visualize_location_data(db, location)
                
            elif choice == "9":
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
                
            else:
                print("\nInvalid option. Please try again.")
            
    except Exception as e:
        print(f"\nAn error occurred: {str(e)}")
        
    finally:
        db.close()

if __name__ == "__main__":
    main() 