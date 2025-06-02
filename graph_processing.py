import osmnx as ox
import networkx as nx
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
import numpy as np
import logging
import time
from datetime import datetime
import csv
from collections import defaultdict
from functools import lru_cache

def get_coordinates(address):
    """Convert address to coordinates using Nominatim geocoder."""
    geolocator = Nominatim(user_agent="road_edge_cover")
    location = geolocator.geocode(address)
    if location:
        return (location.latitude, location.longitude)
    else:
        raise ValueError("Address not found")

def get_road_network(center_point, distance):
    """Get road network within specified distance from center point."""
    # Get the initial directed graph
    G_directed = ox.graph_from_point(center_point, dist=distance, dist_type='network', network_type='drive')
    
    # Convert to undirected while preserving the length attribute
    G = G_directed.to_undirected(reciprocal=False, as_view=False)
    
    # Get the largest connected component
    largest_cc = max(nx.connected_components(G), key=len)
    G = G.subgraph(largest_cc).copy()
    
    # Log the graph sizes and components for debugging
    components = list(nx.connected_components(G))
    logging.info(f"Original directed graph: {len(G_directed.nodes())} nodes, {len(G_directed.edges())} edges")
    logging.info(f"Undirected graph: {len(G.nodes())} nodes, {len(G.edges())} edges")
    logging.info(f"Number of connected components: {len(components)}")
    logging.info(f"Largest component size: {len(largest_cc)} nodes")
    
    # Log information about smaller components
    if len(components) > 1:
        logging.warning("Graph has multiple connected components:")
        for i, comp in enumerate(components):
            if comp != largest_cc:
                logging.warning(f"Component {i+1}: {len(comp)} nodes")
    
    return G

def has_edge(G, node1, node2):
    """Safely check if an edge exists between two nodes."""
    try:
        return node2 in G[node1]
    except KeyError:
        return False

def get_edge_data(G, node1, node2):
    """Safely get edge data between two nodes in an undirected graph."""
    try:
        return G[node1][node2][0]
    except (KeyError, IndexError):
        return None

def precompute_shortest_paths(G):
    """Pre-compute all shortest paths and their lengths in the graph.
    
    Returns:
        tuple: (path_dict, length_dict) where:
            path_dict: Dictionary mapping (source, target) to shortest path
            length_dict: Dictionary mapping (source, target) to path length
    """
    logging.info("Pre-computing all shortest paths...")
    start_time = time.time()
    
    path_dict = {}
    length_dict = {}
    nodes = list(G.nodes())
    n = len(nodes)
    
    # Use Dijkstra's algorithm for each node pair instead of Floyd-Warshall
    # This is more reliable for road networks
    for i, source in enumerate(nodes):
        if i % 100 == 0:
            logging.info(f"Computing paths from node {i}/{n}")
        
        # Compute shortest paths from this source to all other nodes
        try:
            paths = nx.single_source_dijkstra_path(G, source, weight='length')
            lengths = nx.single_source_dijkstra_path_length(G, source, weight='length')
            
            for target, path in paths.items():
                if source != target:
                    path_dict[(source, target)] = path
                    length_dict[(source, target)] = lengths[target]
        except Exception as e:
            logging.error(f"Error computing paths from node {source}: {str(e)}")
            continue
    
    # Verify we have paths
    total_possible = n * (n - 1)  # n nodes, each can reach n-1 other nodes
    total_computed = len(path_dict)
    logging.info(f"Computed {total_computed} out of {total_possible} possible paths")
    
    # Sample some paths to verify
    sample_size = min(5, total_computed)
    if sample_size > 0:
        sample_paths = list(path_dict.items())[:sample_size]
        for (source, target), path in sample_paths:
            length = length_dict[(source, target)]
            logging.info(f"Sample path: {source} -> {target}, length: {length:.1f}m, path length: {len(path)}")
    
    end_time = time.time()
    logging.info(f"Pre-computed {len(path_dict)} shortest paths in {end_time - start_time:.1f} seconds")
    return path_dict, length_dict

def calculate_edge_cover(G, start_node, max_distance, stop_after_priority=False):
    """Calculate an edge cover solution that starts and ends at the start node.
    Each cycle's total distance must not exceed max_distance.
    
    Args:
        G: NetworkX graph
        start_node: Node to start and end at
        max_distance: Maximum distance for each cycle
        stop_after_priority: If True, stop after covering all priority edges
    """
    start_time = time.time()
    logging.info(f"Starting edge cover calculation with {len(G.edges())} edges")
    
    # Verify start node is in the graph
    if start_node not in G:
        raise ValueError(f"Start node {start_node} not found in the graph")
    
    # Verify start node is in the largest component
    largest_cc = max(nx.connected_components(G), key=len)
    if start_node not in largest_cc:
        raise ValueError(f"Start node {start_node} is not in the largest connected component")
    
    # Pre-compute all shortest paths
    path_dict, length_dict = precompute_shortest_paths(G)
    
    # Verify we have paths from start node
    start_paths = [(target, length) for (source, target), length in length_dict.items() 
                  if source == start_node]
    if not start_paths:
        raise ValueError(f"No paths found from start node {start_node}")
    logging.info(f"Found {len(start_paths)} paths from start node")
    
    # Pre-compute edge data and accessibility
    edge_data_cache = {}
    edge_accessibility = {}
    unreachable_edges_info = []
    
    logging.info("Pre-computing edge data and accessibility...")
    for edge in G.edges():
        edge_data = get_edge_data(G, edge[0], edge[1])
        if edge_data is None or 'length' not in edge_data:
            continue
            
        edge_data_cache[edge] = edge_data
        edge_length = edge_data['length']
        
        try:
            # Calculate minimum cycle distance for this edge using pre-computed paths
            dist_to_start = length_dict.get((start_node, edge[0]), float('inf'))
            dist_from_end = length_dict.get((edge[1], start_node), float('inf'))
            min_cycle_dist1 = dist_to_start + edge_length + dist_from_end
            
            dist_to_end = length_dict.get((start_node, edge[1]), float('inf'))
            dist_from_start = length_dict.get((edge[0], start_node), float('inf'))
            min_cycle_dist2 = dist_to_end + edge_length + dist_from_start
            
            min_cycle_dist = min(min_cycle_dist1, min_cycle_dist2)
            edge_accessibility[edge] = min_cycle_dist
            
            # Store information about unreachable edges
            if min_cycle_dist == float('inf'):
                unreachable_edges_info.append({
                    'edge': edge,
                    'dist_to_start': dist_to_start,
                    'dist_from_end': dist_from_end,
                    'dist_to_end': dist_to_end,
                    'dist_from_start': dist_from_start,
                    'edge_length': edge_length,
                    'street_name': edge_data.get('name', 'Unnamed Road')
                })
            else:
                # Log some sample accessible edges
                if len(edge_accessibility) <= 5:
                    logging.info(f"Accessible edge {edge}: min_cycle_dist = {min_cycle_dist:.1f}m")
        except KeyError:
            edge_accessibility[edge] = float('inf')
    
    # Filter edges based on accessibility
    edges_to_cover = []
    unreachable_edges = []
    
    for edge, min_dist in edge_accessibility.items():
        if min_dist <= max_distance:
            priority = edge_data_cache[edge].get('priority', False)
            edges_to_cover.append((edge, priority))
        else:
            unreachable_edges.append((edge, min_dist))
    
    total_edges = len(edges_to_cover)
    if unreachable_edges:
        logging.warning(f"Found {len(unreachable_edges)} unreachable or too distant edges")
        for edge, dist in unreachable_edges[:5]:
            if dist == float('inf'):
                edge_info = next((info for info in unreachable_edges_info if info['edge'] == edge), None)
                if edge_info:
                    logging.warning(f"Edge {edge} ({edge_info['street_name']}) is not reachable from start node:")
                    logging.warning(f"  Distance to start: {edge_info['dist_to_start']:.1f}m")
                    logging.warning(f"  Distance from end: {edge_info['dist_from_end']:.1f}m")
                    logging.warning(f"  Edge length: {edge_info['edge_length']:.1f}m")
            else:
                logging.warning(f"Edge {edge} requires minimum cycle distance of {dist:.1f}m (exceeds limit of {max_distance}m)")
        if len(unreachable_edges) > 5:
            logging.warning(f"... and {len(unreachable_edges) - 5} more unreachable edges")
    
    logging.info(f"Proceeding with {total_edges} accessible edges out of {len(G.edges())} total edges")
    
    # If no edges are accessible, raise an error with detailed information
    if total_edges == 0:
        error_msg = "No accessible edges found. This could be due to:\n"
        error_msg += "1. The start node is not properly connected to the road network\n"
        error_msg += "2. The max_distance is too small\n"
        error_msg += "3. The road network is not properly connected\n"
        error_msg += f"Start node: {start_node}\n"
        error_msg += f"Max distance: {max_distance}m\n"
        error_msg += f"Total edges in graph: {len(G.edges())}\n"
        error_msg += f"Total nodes in graph: {len(G.nodes())}\n"
        error_msg += f"Number of connected components: {len(list(nx.connected_components(G)))}\n"
        raise ValueError(error_msg)
    
    def add_path_to_cycle(path, current_cycle, current_distance):
        """Helper function to add a path to the current cycle, ensuring edges exist."""
        new_cycle = current_cycle.copy()
        new_distance = current_distance
        
        for i in range(len(path) - 1):
            edge_data = edge_data_cache.get((path[i], path[i+1]))
            if edge_data is None:
                try:
                    # Use pre-computed path
                    intermediate_path = path_dict.get((path[i], path[i+1]))
                    if intermediate_path is None:
                        return None, None
                        
                    for j in range(1, len(intermediate_path)-1):
                        new_cycle.append(intermediate_path[j])
                        inter_edge_data = edge_data_cache.get((intermediate_path[j-1], intermediate_path[j]))
                        if inter_edge_data is None:
                            return None, None
                        new_distance += inter_edge_data['length']
                        if new_distance > max_distance:
                            return None, None
                except KeyError:
                    return None, None
            
            new_cycle.append(path[i+1])
            if edge_data:
                new_distance += edge_data['length']
                if new_distance > max_distance:
                    return None, None
        
        return new_cycle, new_distance
    
    # Initialize solution
    cycles = []
    current_cycle = []
    current_distance = 0
    edges_covered = 0
    last_progress_time = time.time()
    progress_interval = 10
    
    while edges_to_cover:
        current_time = time.time()
        if current_time - last_progress_time >= progress_interval:
            elapsed_time = current_time - start_time
            edges_remaining = len(edges_to_cover)
            completion_percentage = ((total_edges - edges_remaining) / total_edges) * 100
            avg_time_per_edge = elapsed_time / (total_edges - edges_remaining) if edges_remaining < total_edges else 0
            estimated_remaining_time = avg_time_per_edge * edges_remaining if avg_time_per_edge > 0 else 0
            
            logging.info(f"Progress: {completion_percentage:.1f}% complete")
            logging.info(f"Edges remaining: {edges_remaining}/{total_edges}")
            logging.info(f"Time elapsed: {elapsed_time:.1f} seconds")
            logging.info(f"Estimated time remaining: {estimated_remaining_time:.1f} seconds")
            logging.info(f"Current cycle length: {len(current_cycle)} nodes")
            last_progress_time = current_time

        if not current_cycle:
            current_cycle = [start_node]
            current_distance = 0
            logging.info(f"Starting new cycle {len(cycles) + 1}")
        
        # Find the nearest uncovered edge that won't exceed distance limit
        min_dist = float('inf')
        next_edge = None
        current_node = current_cycle[-1]
        best_path = None
        
        # First try to find a priority edge
        priority_edges = [(edge, priority) for edge, priority in edges_to_cover if priority]
        if priority_edges:
            for edge, _ in priority_edges:
                if current_node in edge:
                    edge_data = edge_data_cache[edge]
                    edge_distance = edge_data['length']
                    
                    next_node = edge[1] if current_node == edge[0] else edge[0]
                    try:
                        return_distance = length_dict.get((next_node, start_node), float('inf'))
                        total_distance = current_distance + edge_distance + return_distance
                        
                        if total_distance <= max_distance:
                            next_edge = edge
                            best_path = [current_node, next_node]
                            break
                    except KeyError:
                        continue
                else:
                    try:
                        path1 = path_dict.get((current_node, edge[0]))
                        path2 = path_dict.get((current_node, edge[1]))
                        
                        if path1 is None or path2 is None:
                            continue
                            
                        dist1 = length_dict.get((current_node, edge[0]), float('inf'))
                        dist2 = length_dict.get((current_node, edge[1]), float('inf'))
                        
                        edge_distance = edge_data_cache[edge]['length']
                        
                        return_dist1 = length_dict.get((edge[1], start_node), float('inf'))
                        return_dist2 = length_dict.get((edge[0], start_node), float('inf'))
                        
                        total_dist1 = current_distance + dist1 + edge_distance + return_dist1
                        total_dist2 = current_distance + dist2 + edge_distance + return_dist2
                        
                        if total_dist1 <= max_distance and total_dist1 < min_dist:
                            min_dist = total_dist1
                            next_edge = edge
                            best_path = path1 + [edge[1]]
                        if total_dist2 <= max_distance and total_dist2 < min_dist:
                            min_dist = total_dist2
                            next_edge = edge
                            best_path = path2 + [edge[0]]
                    except KeyError:
                        continue
        
        # If no priority edge found and we're not stopping after priority edges, look for any edge
        if not next_edge and not stop_after_priority:
            for edge, _ in edges_to_cover:
                if current_node in edge:
                    edge_data = edge_data_cache[edge]
                    edge_distance = edge_data['length']
                    
                    next_node = edge[1] if current_node == edge[0] else edge[0]
                    try:
                        return_distance = length_dict.get((next_node, start_node), float('inf'))
                        total_distance = current_distance + edge_distance + return_distance
                        
                        if total_distance <= max_distance:
                            next_edge = edge
                            best_path = [current_node, next_node]
                            break
                    except KeyError:
                        continue
                else:
                    try:
                        path1 = path_dict.get((current_node, edge[0]))
                        path2 = path_dict.get((current_node, edge[1]))
                        
                        if path1 is None or path2 is None:
                            continue
                            
                        dist1 = length_dict.get((current_node, edge[0]), float('inf'))
                        dist2 = length_dict.get((current_node, edge[1]), float('inf'))
                        
                        edge_distance = edge_data_cache[edge]['length']
                        
                        return_dist1 = length_dict.get((edge[1], start_node), float('inf'))
                        return_dist2 = length_dict.get((edge[0], start_node), float('inf'))
                        
                        total_dist1 = current_distance + dist1 + edge_distance + return_dist1
                        total_dist2 = current_distance + dist2 + edge_distance + return_dist2
                        
                        if total_dist1 <= max_distance and total_dist1 < min_dist:
                            min_dist = total_dist1
                            next_edge = edge
                            best_path = path1 + [edge[1]]
                        if total_dist2 <= max_distance and total_dist2 < min_dist:
                            min_dist = total_dist2
                            next_edge = edge
                            best_path = path2 + [edge[0]]
                    except KeyError:
                        continue
        
        if next_edge and best_path:
            try:
                new_cycle, new_distance = add_path_to_cycle(best_path, current_cycle.copy(), current_distance)
                if new_cycle is None:
                    logging.error(f"Failed to add path for edge {next_edge}")
                    edges_to_cover = [(e, p) for e, p in edges_to_cover if e != next_edge]
                    continue
                    
                current_cycle = new_cycle
                current_distance = new_distance
                edges_to_cover = [(e, p) for e, p in edges_to_cover if e != next_edge]
                edges_covered += 1
                
                if edges_covered % 10 == 0:
                    logging.info(f"Covered {edges_covered} edges, current cycle distance: {current_distance:.1f}m")
            
            except Exception as e:
                logging.error(f"Error adding edge {next_edge}: {str(e)}")
                edges_to_cover = [(e, p) for e, p in edges_to_cover if e != next_edge]
                continue
        else:
            # Complete current cycle by returning to start node
            if current_cycle[-1] != start_node:
                try:
                    path = path_dict.get((current_cycle[-1], start_node))
                    if path is None:
                        logging.error(f"No path found back to start node from {current_cycle[-1]}")
                        cycles.append(current_cycle)
                        current_cycle = []
                        continue
                        
                    new_cycle, new_distance = add_path_to_cycle(path, current_cycle.copy(), current_distance)
                    if new_cycle is not None and new_distance <= max_distance:
                        current_cycle = new_cycle
                        current_distance = new_distance
                        logging.info(f"Completed cycle {len(cycles) + 1}: {len(current_cycle)} nodes, {current_distance:.1f}m")
                    else:
                        logging.error(f"Could not complete cycle - would exceed maximum distance {max_distance}m")
                        cycles.append(current_cycle)
                        current_cycle = []
                        continue
                except KeyError:
                    logging.error(f"No path found back to start node from {current_cycle[-1]}")
            
            cycles.append(current_cycle)
            current_cycle = []
            
            if stop_after_priority and not any(priority for _, priority in edges_to_cover):
                logging.info("All priority edges covered, stopping as requested")
                break
    
    # Complete the last cycle if needed
    if current_cycle:
        logging.info("Completing final cycle")
        if current_cycle[-1] != start_node:
            try:
                path = path_dict.get((current_cycle[-1], start_node))
                if path is None:
                    logging.error("Could not complete final cycle - no path to start node")
                    cycles.append(current_cycle)
                    return cycles
                    
                new_cycle, new_distance = add_path_to_cycle(path, current_cycle.copy(), current_distance)
                if new_cycle is not None and new_distance <= max_distance:
                    current_cycle = new_cycle
                    current_distance = new_distance
                    logging.info(f"Final cycle completed: {len(current_cycle)} nodes, {current_distance:.1f}m")
                else:
                    logging.error("Could not complete final cycle - would exceed maximum distance")
            except KeyError:
                logging.error("Could not complete final cycle - no path to start node")
        cycles.append(current_cycle)
    
    end_time = time.time()
    total_time = end_time - start_time
    logging.info(f"Edge cover calculation completed in {total_time:.1f} seconds")
    logging.info(f"Created {len(cycles)} cycles")
    
    return cycles

def generate_coverage_table(G, cycles):
    """Generate a table showing how many times each edge appears in each cycle."""
    # Create a dictionary to store edge appearances in each cycle
    edge_appearances = defaultdict(lambda: defaultdict(int))
    
    # Create a mapping of edges to their street names (if available)
    edge_names = {}
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        name = data.get('name', 'Unnamed Road')
        if isinstance(name, list):  # Some OSM ways have multiple names
            name = ' / '.join(name)
        length = data.get('length', 0)
        edge_names[edge] = {
            'name': name,
            'length': length,
            'start_node': edge[0],
            'end_node': edge[1]
        }
    
    # Count appearances of each edge in each cycle
    for cycle_idx, cycle in enumerate(cycles, 1):
        for i in range(len(cycle)-1):
            edge = tuple(sorted([cycle[i], cycle[i+1]]))
            edge_appearances[edge][f'Cycle_{cycle_idx}'] += 1
    
    # Create the CSV file
    with open('edge_coverage.csv', 'w', newline='', encoding='utf-8') as f:
        # Prepare headers
        headers = ['Edge_ID', 'Street_Name', 'Start_Node', 'End_Node', 'Length_m', 'Total_Appearances']
        headers.extend([f'Cycle_{i}' for i in range(1, len(cycles) + 1)])
        
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        
        # Write data for each edge
        for edge, appearances in edge_appearances.items():
            edge_info = edge_names[edge]
            row = {
                'Edge_ID': f'{edge[0]}-{edge[1]}',
                'Street_Name': edge_info['name'],
                'Start_Node': edge_info['start_node'],
                'End_Node': edge_info['end_node'],
                'Length_m': f"{edge_info['length']:.1f}",
                'Total_Appearances': sum(appearances.values())
            }
            # Add cycle-specific appearances
            for i in range(1, len(cycles) + 1):
                row[f'Cycle_{i}'] = appearances.get(f'Cycle_{i}', 0)
            
            writer.writerow(row)
    
    # Calculate coverage statistics
    coverage_stats = {
        'total_edges': len(edge_names),
        'edges_used': len(edge_appearances),
        'max_appearances': max(sum(appearances.values()) for appearances in edge_appearances.values()),
        'avg_appearances': sum(sum(appearances.values()) for appearances in edge_appearances.values()) / len(edge_appearances) if edge_appearances else 0
    }
    
    return coverage_stats

def calculate_cycle_metrics(G, cycle, max_distance):
    """Calculate metrics for a single cycle."""
    cycle_distance = 0
    edges_covered = set()
    edge_lengths = 0
    
    for i in range(len(cycle)-1):
        edge = tuple(sorted([cycle[i], cycle[i+1]]))
        edge_data = get_edge_data(G, cycle[i], cycle[i+1])
        if edge_data:
            cycle_distance += edge_data['length']
            if edge not in edges_covered:
                edges_covered.add(edge)
                edge_lengths += edge_data['length']
    
    return {
        'total_distance': cycle_distance,
        'unique_edge_length': edge_lengths,
        'edges_covered': len(edges_covered),
        'utilization': cycle_distance / max_distance if max_distance > 0 else 1,
        'efficiency': edge_lengths / cycle_distance if cycle_distance > 0 else 0
    }

def analyze_excluded_edges(G, start_node, covered_edges, max_distance):
    """Analyze excluded edges to verify their exclusion is valid."""
    excluded_metrics = []
    
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        if edge not in covered_edges:
            edge_length = data.get('length', 0)
            try:
                # Check if this is a loop edge (same start and end node)
                is_loop = u == v
                
                if is_loop:
                    # For a loop, we only need to get to the node and back once
                    try:
                        dist_to_node = nx.shortest_path_length(G, start_node, u, weight='length')
                        path = nx.shortest_path(G, start_node, u, weight='length')
                        # Total distance is: distance to node + loop length + distance back (same as distance to)
                        total_dist = dist_to_node + edge_length + dist_to_node
                        best_path = path + [u]  # Add the node again to represent the loop
                        best_dist = total_dist
                    except nx.NetworkXNoPath:
                        raise nx.NetworkXNoPath("No path to loop node")
                else:
                    # Regular edge calculations
                    path1 = nx.shortest_path(G, start_node, u, weight='length')
                    dist_to_u = nx.shortest_path_length(G, start_node, u, weight='length')
                    dist_from_v = nx.shortest_path_length(G, v, start_node, weight='length')
                    total_dist1 = dist_to_u + edge_length + dist_from_v
                    
                    path2 = nx.shortest_path(G, start_node, v, weight='length')
                    dist_to_v = nx.shortest_path_length(G, start_node, v, weight='length')
                    dist_from_u = nx.shortest_path_length(G, u, start_node, weight='length')
                    total_dist2 = dist_to_v + edge_length + dist_from_u
                    
                    # Get the shorter path
                    if total_dist1 <= total_dist2:
                        best_path = path1 + [v]
                        best_dist = total_dist1
                    else:
                        best_path = path2 + [u]
                        best_dist = total_dist2
                
                # Get street name
                street_name = data.get('name', 'Unnamed Road')
                if isinstance(street_name, list):
                    street_name = ' / '.join(street_name)
                
                excluded_metrics.append({
                    'edge': edge,
                    'street_name': street_name,
                    'edge_length': edge_length,
                    'total_distance': best_dist,
                    'path': best_path,
                    'is_loop': is_loop,
                    'reason': 'Distance exceeds limit' if best_dist > max_distance else 'Unknown'
                })
            except nx.NetworkXNoPath:
                excluded_metrics.append({
                    'edge': edge,
                    'street_name': street_name if 'street_name' in locals() else 'Unnamed Road',
                    'edge_length': edge_length,
                    'total_distance': float('inf'),
                    'path': None,
                    'is_loop': is_loop if 'is_loop' in locals() else False,
                    'reason': 'No valid path exists'
                })
    
    # Sort by total distance
    excluded_metrics.sort(key=lambda x: x['total_distance'])
    return excluded_metrics

def calculate_solution_metrics(G, cycles, start_node, max_distance):
    """Calculate metrics about the solution including theoretical bounds."""
    # Calculate original metrics
    metrics = {
        'total_distance': 0,
        'lower_bound': 0,
        'upper_bound': 0,
        'num_cycles': len(cycles),
        'edges_covered': 0,
        'total_edges': 0,
        'efficiency_vs_lower': 0,
        'efficiency_vs_upper': 0,
        'max_distance': max_distance  # Add max_distance to metrics
    }
    
    # Calculate cycle-specific metrics and track unique edges covered
    cycle_metrics = []
    all_edges_covered = set()
    
    for i, cycle in enumerate(cycles):
        cycle_stats = calculate_cycle_metrics(G, cycle, max_distance)
        cycle_metrics.append({
            'cycle_number': i + 1,
            **cycle_stats
        })
        metrics['total_distance'] += cycle_stats['total_distance']
        
        # Track edges covered in this cycle
        for j in range(len(cycle)-1):
            edge = tuple(sorted([cycle[j], cycle[j+1]]))
            all_edges_covered.add(edge)
    
    # Sort cycles by cycle number (they should already be in order, but let's make it explicit)
    cycle_metrics.sort(key=lambda x: x['cycle_number'])
    
    # Calculate overall metrics
    all_edges = set()
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        if edge not in all_edges:
            all_edges.add(edge)
            metrics['lower_bound'] += data['length']
    
    # Update edges covered metrics
    metrics['edges_covered'] = len(all_edges_covered)
    metrics['total_edges'] = len(all_edges)
    
    # Analyze excluded edges
    excluded_metrics = analyze_excluded_edges(G, start_node, all_edges_covered, max_distance)
    
    # Calculate upper bound and other metrics as before
    edges_processed = set()
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        if edge not in edges_processed:
            edges_processed.add(edge)
            try:
                dist_to_u = nx.shortest_path_length(G, start_node, u, weight='length')
                dist_to_v = nx.shortest_path_length(G, start_node, v, weight='length')
                dist_from_u = nx.shortest_path_length(G, u, start_node, weight='length')
                dist_from_v = nx.shortest_path_length(G, v, start_node, weight='length')
                
                min_path = min(
                    dist_to_u + data['length'] + dist_from_v,
                    dist_to_v + data['length'] + dist_from_u
                )
                metrics['upper_bound'] += min_path
            except nx.NetworkXNoPath:
                logging.warning(f"No path found for edge {edge} in upper bound calculation")
                continue
    
    # Add coverage analysis
    coverage_stats = generate_coverage_table(G, cycles)
    
    # Calculate overall efficiency metrics
    metrics['efficiency_vs_lower'] = metrics['total_distance'] / metrics['lower_bound'] if metrics['lower_bound'] > 0 else float('inf')
    metrics['efficiency_vs_upper'] = metrics['total_distance'] / metrics['upper_bound'] if metrics['upper_bound'] > 0 else float('inf')
    metrics['coverage_stats'] = coverage_stats
    metrics['cycle_metrics'] = cycle_metrics
    metrics['excluded_metrics'] = excluded_metrics
    
    return metrics 