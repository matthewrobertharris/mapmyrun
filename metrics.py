def print_excluded_metrics(excluded_metrics, max_distance):
    """Print detailed metrics about excluded edges."""
    print("\nExcluded Edges Analysis:")
    print("=" * 50)
    print(f"Total excluded edges: {len(excluded_metrics)}")
    
    unreachable = sum(1 for m in excluded_metrics if m['total_distance'] == float('inf'))
    too_far = sum(1 for m in excluded_metrics if m['total_distance'] != float('inf') and m['total_distance'] > max_distance)
    other = len(excluded_metrics) - unreachable - too_far
    loops = sum(1 for m in excluded_metrics if m['is_loop'])
    
    print(f"Unreachable edges: {unreachable}")
    print(f"Too distant edges: {too_far}")
    print(f"Loop edges: {loops}")
    print(f"Other excluded edges: {other}")
    
    print("\nDetailed Edge Analysis:")
    print("-" * 50)
    for metric in excluded_metrics:
        print(f"\nStreet: {metric['street_name']}")
        print(f"Edge: {metric['edge'][0]} → {metric['edge'][1]}")
        print(f"Edge length: {metric['edge_length']:.1f}m")
        if metric['is_loop']:
            print("Type: Loop edge (same start and end node)")
        if metric['total_distance'] == float('inf'):
            print("Status: Unreachable")
        else:
            print(f"Total cycle distance: {metric['total_distance']:.1f}m")
            print(f"Exceeds limit by: {metric['total_distance'] - max_distance:.1f}m")
            if metric['path']:
                print(f"Best path: {' → '.join(str(n) for n in metric['path'])}")
    print("=" * 50)

def print_metrics(metrics):
    """Print the solution metrics in a formatted way."""
    print("\nSolution Metrics:")
    print("=" * 50)
    print(f"Total distance traveled: {metrics['total_distance']:.1f} meters")
    print(f"Lower bound (sum of edges): {metrics['lower_bound']:.1f} meters")
    print(f"Upper bound (2x shortest paths): {metrics['upper_bound']:.1f} meters")
    print(f"Number of cycles: {metrics['num_cycles']}")
    print(f"Edges covered: {metrics['edges_covered']} out of {metrics['total_edges']}")
    print(f"Ratio to lower bound: {metrics['efficiency_vs_lower']:.2f}x")
    print(f"Ratio to upper bound: {metrics['efficiency_vs_upper']:.2f}x")
    
    print("\nCycle Efficiency Analysis:")
    print("-" * 50)
    print("Cycle  Distance(m)  Utilization  Efficiency  Edges")
    print("-" * 50)
    for cycle in metrics['cycle_metrics']:
        print(f"{cycle['cycle_number']:5d}  {cycle['total_distance']:10.1f}  {cycle['utilization']:10.2%}  {cycle['efficiency']:9.2%}  {cycle['edges_covered']:5d}")
    
    print("\nCoverage Analysis:")
    print(f"Maximum appearances of any edge: {metrics['coverage_stats']['max_appearances']}")
    print(f"Average appearances per edge: {metrics['coverage_stats']['avg_appearances']:.2f}")
    print("\nDetailed edge coverage table has been exported to 'edge_coverage.csv'")
    
    # Print excluded edges analysis with the max_distance from the metrics
    print_excluded_metrics(metrics['excluded_metrics'], metrics['max_distance']) 