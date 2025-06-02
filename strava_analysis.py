import os
import gpxpy
import numpy as np
import networkx as nx
from geopy.distance import geodesic
import logging
from collections import defaultdict
import time
import json
from scipy.spatial import cKDTree
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import csv

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Set to DEBUG level
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def read_strava_files(folder_path):
    """Read all GPX files from the Strava folder and extract GPS coordinates.
    Maintains a persistent deduplicated list of points and only processes new files."""
    from scipy.spatial import cKDTree
    
    # File to store deduplicated points and processed files
    cache_file = 'gps_points_cache.json'
    
    # Initialize lists
    all_points = []
    deduplicated_points = []
    processed_files = set()
    
    # Try to load existing cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                cache = json.load(f)
                deduplicated_points = [tuple(point) for point in cache['points']]
                processed_files = set(cache['processed_files'])
                logging.info(f"Loaded {len(deduplicated_points)} deduplicated points from cache")
                logging.info(f"Found {len(processed_files)} previously processed files")
        except Exception as e:
            logging.warning(f"Error loading cache file: {str(e)}")
    
    # Process new files
    new_files = False
    for filename in os.listdir(folder_path):
        if filename.endswith('.gpx') and filename not in processed_files:
            new_files = True
            file_path = os.path.join(folder_path, filename)
            try:
                with open(file_path, 'r') as gpx_file:
                    gpx = gpxpy.parse(gpx_file)
                    
                    # Process points from this file
                    file_points = []
                    for track in gpx.tracks:
                        for segment in track.segments:
                            for point in segment.points:
                                # Validate coordinates
                                if point.latitude is not None and point.longitude is not None:
                                    # Check for valid coordinate ranges
                                    if -90 <= point.latitude <= 90 and -180 <= point.longitude <= 180:
                                        file_points.append((point.latitude, point.longitude))
                                    else:
                                        logging.warning(f"Invalid coordinates in {filename}: lat={point.latitude}, lon={point.longitude}")
                    
                    if not file_points:
                        logging.warning(f"No valid points found in {filename}")
                        continue
                    
                    # Add to all points
                    all_points.extend(file_points)
                    
                    # Process new points against existing deduplicated points
                    logging.info(f"Processing {len(file_points)} points from {filename}")
                    start_time = time.time()
                    
                    if not deduplicated_points:
                        # If no existing points, add all points from this file
                        deduplicated_points.extend(file_points)
                        points_added = len(file_points)
                    else:
                        try:
                            # Convert existing points to numpy array and create KD-tree
                            existing_points_array = np.array(deduplicated_points)
                            kdtree = cKDTree(existing_points_array)
                            
                            # Convert new points to numpy array
                            new_points_array = np.array(file_points)
                            
                            # Find nearest neighbors for all new points at once
                            distances, _ = kdtree.query(new_points_array, k=1)
                            
                            # Convert distances from degrees to meters (approximate)
                            # 1 degree ≈ 111km at the equator
                            # Add a small epsilon to avoid division by zero
                            distances_meters = distances * 111000 + 1e-10
                            
                            # Filter points that are far enough from existing points
                            far_enough = distances_meters > 5
                            new_unique_points = new_points_array[far_enough]
                            
                            # Add new unique points to deduplicated list
                            points_added = len(new_unique_points)
                            if points_added > 0:
                                deduplicated_points.extend(new_unique_points.tolist())
                        except Exception as e:
                            logging.error(f"Error processing points from {filename}: {str(e)}")
                            # Fall back to original method if KD-tree fails
                            for point in file_points:
                                distances = np.array([geodesic(point, existing_point).meters 
                                                   for existing_point in deduplicated_points])
                                if np.all(distances > 5):
                                    deduplicated_points.append(point)
                            points_added = len(file_points)
                    
                    # Mark file as processed
                    processed_files.add(filename)
                    total_time = time.time() - start_time
                    logging.info(f"Completed processing {filename}")
                    logging.info(f"Added {points_added} new unique points")
                    logging.info(f"Total processing time: {total_time:.1f} seconds")
                    logging.info(f"Average speed: {len(file_points)/total_time:.1f} points/second")
                    logging.info(f"Total unique points: {len(deduplicated_points)}")
                    
            except Exception as e:
                logging.error(f"Error reading {filename}: {str(e)}")
    
    # Save updated cache if we processed new files
    if new_files:
        try:
            with open(cache_file, 'w') as f:
                json.dump({
                    'points': deduplicated_points,
                    'processed_files': list(processed_files)
                }, f)
            logging.info(f"Saved {len(deduplicated_points)} deduplicated points to cache")
        except Exception as e:
            logging.error(f"Error saving cache file: {str(e)}")
    
    # Visualize both original and deduplicated points
    visualize_gps_points(all_points, "Original GPS Points", "original_gps_points.html")
    visualize_gps_points(deduplicated_points, "Deduplicated GPS Points", "deduplicated_gps_points.html")
    
    return all_points, deduplicated_points

def find_nearest_node(G, point):
    """Find the nearest node in the graph to a given GPS point."""
    nodes = list(G.nodes())
    node_coords = np.array([(G.nodes[node]['y'], G.nodes[node]['x']) for node in nodes])
    point_coords = np.array([point[0], point[1]])
    
    distances = np.linalg.norm(node_coords - point_coords, axis=1)
    nearest_idx = np.argmin(distances)
    return nodes[nearest_idx]

def preprocess_gps_points(points, min_distance=5):
    """Preprocess GPS points to remove duplicates and points that are too close together.
    min_distance: minimum distance in meters between points"""
    if not points:
        return []
    
    logging.info(f"Starting GPS point preprocessing with {len(points)} points")
    start_time = time.time()
    
    # Convert to numpy array for faster calculations
    points_array = np.array(points)
    
    # Initialize list of unique points
    unique_points = [points_array[0]]
    last_progress_time = time.time()
    progress_interval = 5  # Log progress every 5 seconds
    
    # Process remaining points
    for i, point in enumerate(points_array[1:], 1):
        # Log progress periodically
        current_time = time.time()
        if current_time - last_progress_time >= progress_interval:
            progress_percent = (i / len(points_array)) * 100
            elapsed_time = current_time - start_time
            points_per_second = i / elapsed_time
            estimated_remaining = (len(points_array) - i) / points_per_second if points_per_second > 0 else 0
            
            logging.info(f"Preprocessing progress: {i}/{len(points_array)} points ({progress_percent:.1f}%)")
            logging.info(f"Processing speed: {points_per_second:.1f} points/second")
            logging.info(f"Estimated time remaining: {estimated_remaining:.1f} seconds")
            logging.info(f"Current unique points: {len(unique_points)}")
            last_progress_time = current_time
        
        # Calculate distances to all unique points
        distances = np.array([geodesic(point, unique_point).meters for unique_point in unique_points])
        
        # Only add point if it's far enough from all existing points
        if np.all(distances > min_distance):
            unique_points.append(point)
            
            # Log when we find a unique point
            if len(unique_points) % 100 == 0:
                logging.debug(f"Found {len(unique_points)} unique points so far")
    
    # Log final statistics
    end_time = time.time()
    total_time = end_time - start_time
    reduction_percent = ((len(points) - len(unique_points)) / len(points)) * 100
    
    logging.info(f"Preprocessing completed in {total_time:.1f} seconds")
    logging.info(f"Reduced {len(points)} GPS points to {len(unique_points)} unique points")
    logging.info(f"Reduction: {reduction_percent:.1f}% of points removed")
    logging.info(f"Average processing speed: {len(points)/total_time:.1f} points/second")
    
    return unique_points

def visualize_gps_points(points, title="GPS Points", output_file='gps_points.html', show_route=True):
    """Visualize GPS points on a map.
    
    Args:
        points: List of (lat, lon) tuples
        title: Title for the visualization
        output_file: Name of the output HTML file
        show_route: Whether to connect points with lines to show the route
    """
    import folium
    
    if not points:
        logging.warning("No points to visualize")
        return
    
    # Calculate center point
    points_array = np.array(points)
    center_lat = np.mean(points_array[:, 0])
    center_lon = np.mean(points_array[:, 1])
    
    # Create map
    m = folium.Map(location=[center_lat, center_lon], zoom_start=14)
    
    # Add route line if requested
    if show_route:
        folium.PolyLine(
            locations=points,
            color='blue',
            weight=2,
            opacity=0.7
        ).add_to(m)
    
    # Add points as markers
    for i, point in enumerate(points):
        folium.CircleMarker(
            location=point,
            radius=2,
            color='red',
            fill=True,
            fill_color='red',
            fill_opacity=0.7,
            popup=f"Point {i+1}"
        ).add_to(m)
    
    # Add title and legend
    legend_html = f'''
    <div style="position: fixed; 
                bottom: 50px; right: 50px; width: 200px; height: 120px; 
                border:2px solid grey; z-index:9999; background-color:white;
                padding: 10px; font-size: 14px;">
        <h4 style="margin-top:0">{title}</h4>
        <p><i class="fa fa-circle" style="color:red"></i> GPS Points</p>
        {f'<p><i class="fa fa-line" style="color:blue"></i> Route</p>' if show_route else ''}
        <p>Total points: {len(points)}</p>
    </div>
    '''
    m.get_root().html.add_child(folium.Element(legend_html))
    
    # Save the map
    m.save(output_file)
    logging.info(f"GPS points visualization saved to {output_file}")

def process_edge(args):
    """Process a single edge and return its results."""
    edge, edge_data, gps_tree, gps_points, sample_distance, max_deviation = args
    
    # Get edge geometry and calculate length
    if edge_data and 'geometry' in edge_data[0]:
        edge_coords = [(lat, lon) for lon, lat in edge_data[0]['geometry'].coords]
    else:
        # Get node coordinates from the edge tuple
        u, v = edge
        edge_coords = [
            (edge_data[0]['y'], edge_data[0]['x']),  # Start node
            (edge_data[0]['y'], edge_data[0]['x'])   # End node
        ]
    
    # Calculate edge length
    edge_length = 0
    for i in range(len(edge_coords) - 1):
        edge_length += geodesic(edge_coords[i], edge_coords[i + 1]).meters
    
    # Adaptive sampling: fewer points for longer edges
    num_samples = min(50, int(edge_length / sample_distance) + 1)
    
    # Sample points along the edge
    sampled_points = []
    for i in range(len(edge_coords) - 1):
        start = edge_coords[i]
        end = edge_coords[i + 1]
        segment_length = geodesic(start, end).meters
        segment_samples = int(num_samples * (segment_length / edge_length))
        
        for j in range(segment_samples):
            fraction = j / (segment_samples - 1) if segment_samples > 1 else 0
            lat = start[0] + fraction * (end[0] - start[0])
            lon = start[1] + fraction * (end[1] - start[1])
            sampled_points.append((lat, lon))
    
    # Convert sampled points to numpy array for faster processing
    sampled_points_array = np.array(sampled_points)
    
    # Find nearest GPS points using spatial index
    distances, indices = gps_tree.query(sampled_points_array, k=1)
    
    # Calculate statistics
    avg_deviation = np.mean(distances)
    max_single_deviation = np.max(distances)
    
    # Early termination if average deviation is too high
    if avg_deviation > max_deviation * 2:  # More lenient threshold for early termination
        return {
            'edge': edge,
            'length': edge_length,
            'sampled_points': len(sampled_points),
            'avg_deviation': avg_deviation,
            'max_deviation': max_single_deviation,
            'matched_points': set(),
            'is_valid': False
        }
    
    # Get matched GPS points
    matched_gps_points = set(map(tuple, gps_points[indices]))
    
    return {
        'edge': edge,
        'length': edge_length,
        'sampled_points': len(sampled_points),
        'avg_deviation': avg_deviation,
        'max_deviation': max_single_deviation,
        'matched_points': matched_gps_points,
        'is_valid': avg_deviation <= max_deviation
    }

def match_points_to_edges(G, points, max_deviation=15, sample_distance=10):
    """Match GPS points to edges in the graph.
    max_deviation: maximum average deviation in meters that GPS points can be from the road
    sample_distance: distance in meters between sampled points along the road"""
    from scipy.spatial import cKDTree
    
    valid_edges = set()
    matched_points = set()
    edge_deviations = {}
    edge_max_deviations = {}
    edge_names = {}  # Store road names for each edge
    road_segment_counts = {}  # Track segment numbers for each road name
    
    # Use deduplicated points for matching
    original_points, deduplicated_points = points
    logging.info(f"Using {len(deduplicated_points)} deduplicated GPS points for matching")
    
    # Convert to numpy array for faster distance calculations
    gps_points = np.array(deduplicated_points)
    
    # Create spatial index for GPS points
    logging.info("Creating spatial index for GPS points...")
    gps_tree = cKDTree(gps_points)
    
    # Get total number of edges and calculate total distance
    total_edges = len(G.edges())
    total_distance = 0
    total_sampled_points = 0
    
    # Calculate total distance and points first
    for u, v, data in G.edges(data=True):
        edge_data = G.get_edge_data(u, v)
        if edge_data and 'geometry' in edge_data[0]:
            edge_coords = [(lat, lon) for lon, lat in edge_data[0]['geometry'].coords]
        else:
            edge_coords = [
                (G.nodes[u]['y'], G.nodes[u]['x']),
                (G.nodes[v]['y'], G.nodes[v]['x'])
            ]
        
        # Calculate edge length
        edge_length = 0
        for i in range(len(edge_coords) - 1):
            edge_length += geodesic(edge_coords[i], edge_coords[i + 1]).meters
        
        total_distance += edge_length
        total_sampled_points += int(edge_length / sample_distance) + 1
    
    logging.info(f"Total road network distance: {total_distance:.1f} meters")
    logging.info(f"Total sampled points to check: {total_sampled_points}")
    
    # Process edges
    processed_edges = 0
    processed_distance = 0
    processed_points = 0
    start_time = time.time()
    
    for u, v, data in G.edges(data=True):
        try:
            edge_start_time = time.time()
            edge = tuple(sorted([u, v]))
            processed_edges += 1
            
            # Get edge geometry and calculate length
            edge_data = G.get_edge_data(u, v)
            if edge_data and 'geometry' in edge_data[0]:
                edge_coords = [(lat, lon) for lon, lat in edge_data[0]['geometry'].coords]
            else:
                edge_coords = [
                    (G.nodes[u]['y'], G.nodes[u]['x']),
                    (G.nodes[v]['y'], G.nodes[v]['x'])
                ]
            
            # Get road name and assign segment number
            base_road_name = str(edge_data[0].get('name', 'Unnamed Road'))
            if base_road_name not in road_segment_counts:
                road_segment_counts[base_road_name] = 1
            else:
                road_segment_counts[base_road_name] += 1
            
            road_name = f"{base_road_name}_{road_segment_counts[base_road_name]}"
            edge_names[edge] = road_name
            
            # Calculate edge length
            edge_length = 0
            for i in range(len(edge_coords) - 1):
                edge_length += geodesic(edge_coords[i], edge_coords[i + 1]).meters
            
            processed_distance += edge_length
            
            # Skip very short edges
            if edge_length < 1.0:  # Skip edges shorter than 1 meter
                logging.debug(f"Skipping very short edge {edge} with length {edge_length:.2f}m")
                continue
            
            # Adaptive sampling: fewer points for longer edges
            num_samples = min(50, max(2, int(edge_length / sample_distance) + 1))  # Ensure at least 2 samples
            
            # Sample points along the edge
            sampled_points = []
            for i in range(len(edge_coords) - 1):
                start = edge_coords[i]
                end = edge_coords[i + 1]
                segment_length = geodesic(start, end).meters
                
                # Skip zero-length segments
                if segment_length < 0.1:  # Skip segments shorter than 0.1 meters
                    continue
                
                # Calculate number of samples for this segment
                if edge_length > 0:  # Prevent division by zero
                    segment_samples = max(2, int(num_samples * (segment_length / edge_length)))
                else:
                    segment_samples = 2  # Default to 2 samples if edge length is 0
                
                for j in range(segment_samples):
                    if segment_samples > 1:  # Prevent division by zero
                        fraction = j / (segment_samples - 1)
                    else:
                        fraction = 0
                    lat = start[0] + fraction * (end[0] - start[0])
                    lon = start[1] + fraction * (end[1] - start[1])
                    sampled_points.append((lat, lon))
            
            # Skip edges with no valid samples
            if not sampled_points:
                logging.debug(f"Skipping edge {edge} with no valid samples")
                continue
            
            processed_points += len(sampled_points)
            
            # Convert sampled points to numpy array for faster processing
            sampled_points_array = np.array(sampled_points)
            
            # Find nearest GPS points using spatial index
            distances, indices = gps_tree.query(sampled_points_array, k=1)
            
            # Convert distances from degrees to meters
            # Note: This is an approximation. For more accuracy, we should use geodesic distance
            # but that would be much slower. This approximation is reasonable for small distances.
            distances_meters = distances * 111000  # 1 degree ≈ 111km at the equator
            
            # Calculate statistics
            if len(distances_meters) > 0:  # Ensure we have distances to calculate
                avg_deviation = np.mean(distances_meters)
                max_single_deviation = np.max(distances_meters)
                
                # Log detailed deviation information
                logging.debug(f"Edge {edge} deviations:")
                logging.debug(f"  Min deviation: {np.min(distances_meters):.1f}m")
                logging.debug(f"  Max deviation: {max_single_deviation:.1f}m")
                logging.debug(f"  Avg deviation: {avg_deviation:.1f}m")
                logging.debug(f"  Std deviation: {np.std(distances_meters):.1f}m")
            else:
                logging.debug(f"Skipping edge {edge} with no valid distances")
                continue
            
            # Early termination if average deviation is too high
            if avg_deviation > max_deviation * 2:  # More lenient threshold for early termination
                logging.debug(f"Edge {edge} rejected early due to high average deviation: {avg_deviation:.1f}m")
                continue
            
            # Get matched GPS points
            matched_gps_points = set(map(tuple, gps_points[indices]))
            matched_points.update(matched_gps_points)
            
            # Store deviations for visualization
            edge_deviations[edge] = avg_deviation
            edge_max_deviations[edge] = max_single_deviation
            
            # Log edge completion
            edge_time = time.time() - edge_start_time
            current_time = time.time()
            total_elapsed = current_time - start_time
            
            # Prevent division by zero in speed calculations
            if total_elapsed > 0:
                edges_per_second = processed_edges / total_elapsed
                estimated_remaining = (total_edges - processed_edges) / edges_per_second
            else:
                edges_per_second = 0
                estimated_remaining = 0
            
            logging.info(f"Edge {processed_edges}/{total_edges} completed:")
            logging.info(f"  Road: {road_name}")
            logging.info(f"  Length: {edge_length:.1f}m (Total: {processed_distance:.1f}/{total_distance:.1f}m, {processed_distance/total_distance:.1%})")
            logging.info(f"  Points: {len(sampled_points)} (Total: {processed_points}/{total_sampled_points}, {processed_points/total_sampled_points:.1%})")
            logging.info(f"  Average deviation: {avg_deviation:.1f}m")
            logging.info(f"  Maximum deviation: {max_single_deviation:.1f}m")
            logging.info(f"  Processing time: {edge_time:.2f}s")
            logging.info(f"  Average speed: {edges_per_second:.2f} edges/s")
            logging.info(f"  Estimated time remaining: {estimated_remaining:.1f}s")
            
            if avg_deviation <= max_deviation:
                valid_edges.add(edge)
                logging.info("  Result: Marked as run (within deviation threshold)")
            else:
                logging.info("  Result: Exceeds deviation threshold")
        
        except Exception as e:
            logging.error(f"Error processing edge {edge}: {str(e)}")
            logging.error(f"Edge details: length={edge_length:.2f}m, samples={len(sampled_points)}")
            continue
    
    # Log final statistics
    total_time = time.time() - start_time
    if total_time > 0:  # Prevent division by zero
        edges_per_second = total_edges / total_time
    else:
        edges_per_second = 0
    
    logging.info(f"Completed processing {total_edges} edges in {total_time:.1f} seconds")
    logging.info(f"Total distance processed: {total_distance:.1f} meters")
    logging.info(f"Total points checked: {total_sampled_points}")
    logging.info(f"Found {len(valid_edges)} valid edges ({len(valid_edges)/total_edges:.1%} of total)")
    logging.info(f"Matched {len(matched_points)} GPS points to edges")
    logging.info(f"Average processing speed: {edges_per_second:.2f} edges/second")
    
    # Prepare classification for visualization
    not_run_edges = set()
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        if edge not in valid_edges:
            not_run_edges.add(edge)
    
    # Ensure all edges have names
    for edge in valid_edges.union(not_run_edges):
        if edge not in edge_names:
            edge_data = G.get_edge_data(edge[0], edge[1])
            base_road_name = str(edge_data[0].get('name', 'Unnamed Road'))
            if base_road_name not in road_segment_counts:
                road_segment_counts[base_road_name] = 1
            else:
                road_segment_counts[base_road_name] += 1
            edge_names[edge] = f"{base_road_name}_{road_segment_counts[base_road_name]}"
    
    classification = {
        'run_edges': valid_edges,
        'not_run_edges': not_run_edges,
        'deviation': edge_deviations,
        'max_deviation': edge_max_deviations,
        'road_names': edge_names
    }
    
    return classification, matched_points

def analyze_not_run_segments(G, classification, output_file='segments_to_run.csv'):
    """Analyze and output not-run segments to a CSV file, ordered by length.
    
    Args:
        G: NetworkX graph containing the road network
        classification: Dictionary containing run and not-run edges
        output_file: Name of the output CSV file
    """
    # Create a list to store segment information
    segments = []
    
    # Process each not-run edge
    for edge in classification['not_run_edges']:
        # Get edge data
        edge_data = G.get_edge_data(edge[0], edge[1])
        
        # Get road name
        road_name = classification.get('road_names', {}).get(edge, 'Unnamed Road')
        
        # Calculate edge length
        if edge_data and 'geometry' in edge_data[0]:
            # Use the full geometry if available
            coords = [(lat, lon) for lon, lat in edge_data[0]['geometry'].coords]
            edge_length = 0
            for i in range(len(coords) - 1):
                edge_length += geodesic(coords[i], coords[i + 1]).meters
        else:
            # Fall back to start and end points if no geometry
            start_coord = (G.nodes[edge[0]]['y'], G.nodes[edge[0]]['x'])
            end_coord = (G.nodes[edge[1]]['y'], G.nodes[edge[1]]['x'])
            edge_length = geodesic(start_coord, end_coord).meters
        
        # Get deviation information if available
        avg_deviation = classification.get('deviation', {}).get(edge, 0)
        max_deviation = classification.get('max_deviation', {}).get(edge, 0)
        
        # Add segment information
        segments.append({
            'road_name': road_name,
            'edge_id': f"{edge[0]}->{edge[1]}",
            'length_meters': edge_length,
            'avg_deviation': avg_deviation,
            'max_deviation': max_deviation
        })
    
    # Sort segments by length in descending order
    segments.sort(key=lambda x: x['length_meters'], reverse=True)
    
    # Write to CSV file
    with open(output_file, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['road_name', 'edge_id', 'length_meters', 'avg_deviation', 'max_deviation'])
        writer.writeheader()
        writer.writerows(segments)
    
    # Log summary statistics
    total_length = sum(segment['length_meters'] for segment in segments)
    logging.info(f"Analyzed {len(segments)} not-run segments")
    logging.info(f"Total length of not-run segments: {total_length:.1f} meters")
    logging.info(f"Average segment length: {total_length/len(segments):.1f} meters")
    logging.info(f"Longest segment: {segments[0]['length_meters']:.1f} meters ({segments[0]['road_name']})")
    logging.info(f"Results saved to {output_file}")

def classify_road_segments(G, strava_folder):
    """Classify road segments based on Strava GPS data."""
    # Read Strava GPS data
    points = read_strava_files(strava_folder)
    logging.info(f"Read {len(points)} GPS points from Strava files")
    
    # Match points to edges with stricter requirements
    classification, matched_points = match_points_to_edges(G, points, max_deviation=15, sample_distance=10)
    logging.info(f"Matched {len(matched_points)} points to {len(classification['run_edges'])} edges")
    
    # Analyze not-run segments
    analyze_not_run_segments(G, classification)
    
    # Calculate statistics
    total_edges = len(classification['run_edges']) + len(classification['not_run_edges'])
    run_percentage = (len(classification['run_edges']) / total_edges) * 100 if total_edges > 0 else 0
    
    logging.info(f"Total edges: {total_edges}")
    logging.info(f"Run edges: {len(classification['run_edges'])} ({run_percentage:.1f}%)")
    logging.info(f"Not run edges: {len(classification['not_run_edges'])} ({100 - run_percentage:.1f}%)")
    
    return {
        'run_edges': classification['run_edges'],
        'not_run_edges': classification['not_run_edges'],
        'road_names': classification.get('road_names', {}),  # Include road names with segment numbers
        'deviation': classification.get('deviation', {}),  # Include average deviations
        'max_deviation': classification.get('max_deviation', {}),  # Include maximum deviations
        'total_points': len(points),
        'matched_points': len(matched_points),
        'run_percentage': run_percentage
    }
