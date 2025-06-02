import logging
import osmnx as ox
import networkx as nx
from graph_processing import calculate_edge_cover, calculate_solution_metrics

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def analyze_not_run_edges(G, classification, center_point, max_distance):
    """Analyze and find edge cover for not-run edges.
    
    Args:
        G: Original NetworkX graph
        classification: Dictionary containing run and not-run edges
        center_point: (lat, lon) tuple for the center point
        max_distance: Maximum distance for each cycle
        
    Returns:
        Dictionary containing the solution metrics and cycles
    """
    # Create a copy of the graph
    not_run_G = G.copy()
    
    # Mark not-run edges as priority
    for u, v, data in not_run_G.edges(data=True):
        edge = tuple(sorted([u, v]))
        is_not_run = edge in classification['not_run_edges']
        data['priority'] = is_not_run
    
    # Log graph statistics
    logging.info(f"Created graph with {not_run_G.number_of_nodes()} nodes and {not_run_G.number_of_edges()} edges")
    logging.info(f"Marked {len(classification['not_run_edges'])} edges as priority (not-run)")
    
    return not_run_G 