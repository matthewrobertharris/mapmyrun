import folium
from folium import plugins
import logging
from collections import defaultdict
import numpy as np
import os

def visualize_solution(G, cycles, center_point, metrics, output_file='route_map.html'):
    """Visualize the solution on a map with interactive features."""
    logging.info(f"Starting visualization with {len(cycles)} cycles")
    
    # Create a map centered at the starting point
    m = folium.Map(location=center_point, zoom_start=15)
    
    # Define a color palette for different cycles
    colors = ['#FF4B4B', '#4B4BFF', '#4BFF4B', '#FF4BFF', '#4BFFFF', '#FFFF4B']
    
    # Create feature groups for each cycle and track all layers in order
    cycle_groups = []
    all_layers = []  # Track all layers in the order they're added to the map
    
    # Create a dictionary to track which cycles each edge belongs to
    edge_cycles = defaultdict(list)
    covered_edges = set()
    for cycle_num, path in enumerate(cycles):
        for i in range(len(path)-1):
            edge = tuple(sorted([path[i], path[i+1]]))
            edge_cycles[edge].append(cycle_num + 1)
            covered_edges.add(edge)
    
    # Create a feature group for excluded edges
    excluded_group = folium.FeatureGroup(name='Excluded Edges')
    
    # Add excluded edges to the map
    for u, v, data in G.edges(data=True):
        edge = tuple(sorted([u, v]))
        if edge not in covered_edges:
            try:
                if 'geometry' in data:
                    line_coords = [(coord[1], coord[0]) for coord in data['geometry'].coords]
                else:
                    if 'y' not in G.nodes[u] or 'x' not in G.nodes[u] or \
                       'y' not in G.nodes[v] or 'x' not in G.nodes[v]:
                        continue
                    start_coord = (G.nodes[u]['y'], G.nodes[u]['x'])
                    end_coord = (G.nodes[v]['y'], G.nodes[v]['x'])
                    line_coords = [start_coord, end_coord]
                
                # Get street name
                street_name = data.get('name', 'Unnamed Road')
                if isinstance(street_name, list):
                    street_name = ' / '.join(street_name)
                
                popup_content = f"""
                    <div style='font-family: Arial, sans-serif;'>
                        <h4>{street_name}</h4>
                        <div><b>Status:</b> Excluded from cycles</div>
                        <div><b>Distance:</b> {data.get('length', 0):.1f} meters</div>
                        <div><b>From Node:</b> {u}</div>
                        <div><b>To Node:</b> {v}</div>
                    </div>
                """
                
                line = folium.PolyLine(
                    line_coords,
                    weight=3,
                    color='#808080',  # Gray color for excluded edges
                    opacity=0.6,
                    popup=folium.Popup(popup_content, max_width=300),
                    tooltip=f'{street_name} (Excluded)',
                    dash_array='5, 10'  # Dashed line pattern
                )
                line.add_to(excluded_group)
            except Exception as e:
                logging.error(f"Error processing excluded edge {edge}: {str(e)}")
                continue
    
    # Process cycles
    for cycle_num, path in enumerate(cycles):
        logging.info(f"Processing cycle {cycle_num + 1} with {len(path)} nodes")
        cycle_coords = []
        segment_number = 1
        cycle_distance = 0
        color = colors[cycle_num % len(colors)]
        
        # Create a feature group for this cycle
        feature_group = folium.FeatureGroup(name=f'Cycle {cycle_num + 1}')
        
        for i in range(len(path)-1):
            try:
                edge = tuple(sorted([path[i], path[i+1]]))
                
                # Check if both nodes exist in the graph
                if path[i] not in G or path[i+1] not in G:
                    logging.warning(f"Node {path[i]} or {path[i+1]} not found in graph for cycle {cycle_num + 1}")
                    continue
                
                # Check if edge exists between the nodes
                if path[i+1] not in G[path[i]]:
                    #logging.warning(f"Edge not found between nodes {path[i]} and {path[i+1]} in cycle {cycle_num + 1}")
                    continue
                
                edge_data = G[path[i]][path[i+1]][0]
                
                if 'geometry' in edge_data:
                    line_coords = [(coord[1], coord[0]) for coord in edge_data['geometry'].coords]
                else:
                    if 'y' not in G.nodes[path[i]] or 'x' not in G.nodes[path[i]] or \
                       'y' not in G.nodes[path[i+1]] or 'x' not in G.nodes[path[i+1]]:
                        logging.error(f"Missing coordinates for nodes {path[i]} or {path[i+1]}")
                        continue
                    start_coord = (G.nodes[path[i]]['y'], G.nodes[path[i]]['x'])
                    end_coord = (G.nodes[path[i+1]]['y'], G.nodes[path[i+1]]['x'])
                    line_coords = [start_coord, end_coord]
                
                segment_distance = edge_data['length']
                cycle_distance += segment_distance
                
                # Get street name
                street_name = edge_data.get('name', 'Unnamed Road')
                if isinstance(street_name, list):
                    street_name = ' / '.join(street_name)
                
                # Get all cycles this edge belongs to
                cycles_containing_edge = edge_cycles[edge]
                cycles_info = ', '.join([f'Cycle {c}' for c in cycles_containing_edge])
                
                # Create a color-coded list of cycles
                cycle_list_html = '<div style="margin-top: 5px;">'
                cycle_list_html += '<b>Appears in:</b><br>'
                for cycle_id in cycles_containing_edge:
                    cycle_color = colors[(cycle_id-1) % len(colors)]
                    cycle_list_html += f'<div style="margin-left: 10px; color: {cycle_color};">• Cycle {cycle_id}</div>'
                cycle_list_html += '</div>'
                
                popup_content = f"""
                    <div style='font-family: Arial, sans-serif;'>
                        <h4>{street_name}</h4>
                        <div><b>Current:</b> Cycle {cycle_num + 1} - Segment {segment_number}</div>
                        <div><b>Segment Distance:</b> {segment_distance:.1f} meters</div>
                        <div><b>Cycle Distance:</b> {cycle_distance:.1f} meters</div>
                        <div><b>From Node:</b> {path[i]}</div>
                        <div><b>To Node:</b> {path[i+1]}</div>
                        <div><b>Times Used:</b> {len(cycles_containing_edge)}</div>
                        {cycle_list_html}
                    </div>
                """
                
                line = folium.PolyLine(
                    line_coords,
                    weight=4,
                    color=color,
                    opacity=0.8,
                    popup=folium.Popup(popup_content, max_width=300),
                    tooltip=f'{street_name} (Used in {len(cycles_containing_edge)} cycles)'
                )
                line.add_to(feature_group)
                
                cycle_coords.extend(line_coords)
                segment_number += 1
                
            except Exception as e:
                logging.error(f"Error processing segment {segment_number} in cycle {cycle_num + 1}: {str(e)}")
                continue
        
        feature_group.add_to(m)
        cycle_groups.append(feature_group)
        all_layers.append(feature_group)
    
    # Add excluded edges group after all cycles
    excluded_group.add_to(m)
    all_layers.append(excluded_group)
    
    # Add start/end point marker
    folium.Marker(
        location=center_point,
        popup='<div style="font-family: Arial, sans-serif;"><h4>Start/End Point</h4></div>',
        icon=folium.Icon(color='green', icon='info-sign', prefix='fa'),
        tooltip='Start/End Point'
    ).add_to(m)
    
    # Add custom CSS and JavaScript
    custom_css = """
    <style>
    .custom-legend {
        position: fixed;
        bottom: 50px;
        right: 50px;
        background: white;
        padding: 8px;
        border: 2px solid grey;
        border-radius: 5px;
        max-width: 300px;
        max-height: 50vh;
        overflow-y: auto;
        z-index: 1000;
        font-family: Arial, sans-serif;
        font-size: 12px;
    }
    .metrics-legend {
        position: fixed;
        top: 20px;
        right: 50px;
        background: white;
        padding: 8px;
        border: 2px solid grey;
        border-radius: 5px;
        max-width: 300px;
        max-height: 30vh;
        overflow-y: auto;
        z-index: 1000;
        font-family: Arial, sans-serif;
        font-size: 12px;
    }
    .cycle-item {
        margin: 3px 0;
        display: flex;
        align-items: center;
        line-height: 1.2;
    }
    .color-box {
        width: 25px;
        height: 3px;
        margin: 0 8px;
        display: inline-block;
        flex-shrink: 0;
    }
    .cycle-label {
        flex-grow: 1;
        cursor: pointer;
    }
    .excluded-edge {
        border-top: 2px dashed #808080;
        width: 25px;
        margin: 0 8px;
        flex-shrink: 0;
    }
    .legend-header {
        margin: 0 0 5px 0;
        font-size: 13px;
        font-weight: bold;
    }
    .legend-section {
        margin-bottom: 8px;
    }
    .button-group {
        margin-bottom: 5px;
        display: flex;
        gap: 5px;
    }
    .button-group button {
        padding: 2px 5px;
        font-size: 11px;
    }
    </style>
    """
    
    # Update metrics HTML with line breaks for cycles
    metrics_html = f"""
    <div class="metrics-legend">
        <h4 class="legend-header">Solution Metrics</h4>
        <div class="legend-section">
            <div><b>Total:</b> {metrics['total_distance']:.1f}m</div>
            <div><b>Lower:</b> {metrics['lower_bound']:.1f}m</div>
            <div><b>Upper:</b> {metrics['upper_bound']:.1f}m</div>
            <div><b>Cycles:</b> {metrics['num_cycles']}</div>
            <div><b>Coverage:</b> {metrics['edges_covered']}/{metrics['total_edges']}</div>
        </div>
        <div class="legend-section">
            <div><b>Efficiency:</b></div>
            <div style="margin-left: 8px;">
                Lower: {metrics['efficiency_vs_lower']:.2f}x<br>
                Upper: {metrics['efficiency_vs_upper']:.2f}x
            </div>
        </div>
        <div class="legend-section">
            <div><b>Cycle Stats:</b></div>
            <div style="margin-left: 8px;">
                {'<br>'.join(f"C{c['cycle_number']}: {c['utilization']*100:.0f}% util, {c['efficiency']*100:.0f}% eff" for c in metrics['cycle_metrics'])}
            </div>
        </div>
        <div class="legend-section">
            <div><b>Coverage:</b></div>
            <div style="margin-left: 8px;">
                Max: {metrics['coverage_stats']['max_appearances']}x<br>
                Avg: {metrics['coverage_stats']['avg_appearances']:.1f}x
            </div>
        </div>
    </div>
    """
    
    # Create legend HTML with excluded edges toggle
    legend_html = f"""
    {custom_css}
    <div class="custom-legend">
        <h4 class="legend-header">Route Cycles</h4>
        <div class="button-group">
            <button onclick="document.querySelectorAll('.cycle-toggle').forEach(c => {{c.checked = true; c.dispatchEvent(new Event('change'))}})">
                Show All
            </button>
            <button onclick="document.querySelectorAll('.cycle-toggle').forEach(c => {{c.checked = false; c.dispatchEvent(new Event('change'))}})">
                Hide All
            </button>
        </div>
    """
    
    # Add cycle toggles to legend
    for i, group in enumerate(cycle_groups):
        color = colors[i % len(colors)]
        legend_html += f"""
        <div class="cycle-item">
            <input type="checkbox" 
                   class="cycle-toggle" 
                   checked 
                   onchange="document.querySelector('.leaflet-control-layers-overlays').children[{i}].children[0].click()">
            <div class="color-box" style="background-color: {color}"></div>
            <span class="cycle-label">C{i + 1}</span>
        </div>
        """
    
    # Add excluded edges toggle
    legend_html += f"""
        <div class="cycle-item" style="margin-top: 8px; border-top: 1px solid #ccc; padding-top: 8px;">
            <input type="checkbox" 
                   class="cycle-toggle" 
                   checked 
                   onchange="document.querySelector('.leaflet-control-layers-overlays').children[{len(cycle_groups)}].children[0].click()">
            <div class="excluded-edge"></div>
            <span class="cycle-label">Excluded</span>
        </div>
        <div class="cycle-item">
            <i class="fa fa-info-sign" style="color: green; margin: 0 8px; width: 25px; text-align: center;"></i>
            <span>Start/End</span>
        </div>
    </div>
    """
    
    # Add the legends to the map
    m.get_root().html.add_child(folium.Element(metrics_html))
    m.get_root().html.add_child(folium.Element(legend_html))
    
    # Add layer control (but hide it)
    folium.LayerControl().add_to(m)
    
    # Add fullscreen control
    plugins.Fullscreen().add_to(m)
    
    # Add CSS to hide the default layer control
    m.get_root().html.add_child(folium.Element("""
        <style>
        .leaflet-control-layers {
            display: none;
        }
        </style>
    """))
    
    m.save(output_file) 