import logging
import osmnx as ox
from visualization import visualize_solution, visualize_road_network, visualize_classification
from metrics import print_metrics
from graph_processing import (
    get_coordinates,
    get_road_network,
    calculate_edge_cover,
    calculate_solution_metrics
)
from strava_analysis import classify_road_segments
from not_run_analysis import analyze_not_run_edges

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def main():
    # Get input from user
    #address = input("Enter the starting address: ")
    #distance = float(input("Enter the distance in meters: "))
    #address = "45 Queens Road, Lambton, NSW, Australia"
    distance = 2000 # This is the maximum distance of roads to be considered
    max_distance = 5000 # Maximum distance for each cycle
    
    try:
        # Convert address to coordinates
        #center_point = get_coordinates(address)
        center_point = (-32.92923984, 151.71086021)
        print(f"Coordinates: {center_point}")
        
        # Get road network
        G = get_road_network(center_point, distance)
        print("Road network retrieved successfully")
        visualize_road_network(G)

        # Analyze Strava data
        print("Analyzing Strava data...")
        strava_folder = "strava"  # Folder containing Strava GPX files
        classification = classify_road_segments(G, strava_folder)
        visualize_classification(G, classification)
        print("Road classification visualization saved to 'road_classification.html'")
        
        # Find nearest node to start point
        start_node = ox.nearest_nodes(G, center_point[1], center_point[0])
        
        # Calculate edge cover solution with maximum distance constraint
        print("Calculating edge cover solution...")
        cycles = calculate_edge_cover(G, start_node, max_distance)
        
        # Calculate solution metrics
        print("Calculating solution metrics...")
        metrics = calculate_solution_metrics(G, cycles, start_node, max_distance)
        print_metrics(metrics)
        
        # Visualize solution
        print("Generating map visualization...")
        visualize_solution(G, cycles, center_point, metrics)
        print("Solution has been saved to 'route_map.html'")
        
        # Analyze not-run edges
        print("\nAnalyzing not-run edges...")
        not_run_G  = analyze_not_run_edges(G, classification, center_point, max_distance)
        
        # Find nearest node to start point
        not_run_start_node = ox.nearest_nodes(not_run_G, center_point[1], center_point[0])
        
        # Calculate edge cover solution with maximum distance constraint
        logging.info("Calculating edge cover solution for not-run edges...")
        not_run_cycles = calculate_edge_cover(not_run_G, not_run_start_node, max_distance, stop_after_priority=True)
        
        # Calculate solution metrics
        logging.info("Calculating solution metrics for not-run edges...")
        not_run_metrics = calculate_solution_metrics(not_run_G, not_run_cycles, start_node, max_distance)
        print_metrics(not_run_metrics)
        
        # Visualize not-run solution
        print("Generating map visualization for not-run edges...")
        visualize_solution(G, not_run_cycles, center_point, not_run_metrics, output_file='not_run_route_map.html')
        print("Not-run solution has been saved to 'not_run_route_map.html'")
        
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main() 