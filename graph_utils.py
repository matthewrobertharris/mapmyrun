import networkx as nx
from collections import defaultdict
import copy
import math

def deep_copy_multidigraph(
    original_graph: nx.MultiDiGraph,
    node_attrs: list[str] = None,
    edge_attrs: list[str] = None,
    graph_attrs: list[str] = None
) -> nx.MultiDiGraph:
    """
    Performs a deep copy of a MultiDiGraph, selectively copying specified attributes.

    Parameters:
        original_graph (nx.MultiDiGraph): The graph to copy.
        node_attrs (list[str], optional): List of node attributes to copy.
        edge_attrs (list[str], optional): List of edge attributes to copy.
        graph_attrs (list[str], optional): List of graph-level attributes to copy.

    Returns:
        nx.MultiDiGraph: A new graph with the selected attributes copied.
    """
    new_graph = nx.MultiDiGraph()

    # Copy selected graph-level attributes
    if graph_attrs:
        new_graph.graph = {k: copy.deepcopy(v) for k, v in original_graph.graph.items() if k in graph_attrs}

    # Copy nodes with selected attributes
    for node, attr in original_graph.nodes(data=True):
        filtered_attr = {k: copy.deepcopy(v) for k, v in attr.items() if not node_attrs or k in node_attrs}
        new_graph.add_node(node, **filtered_attr)

    # Copy edges with selected attributes
    for u, v, key, attr in original_graph.edges(keys=True, data=True):
        filtered_attr = {k: copy.deepcopy(v) for k, v in attr.items() if not edge_attrs or k in edge_attrs}
        new_graph.add_edge(u, v, key=key, **filtered_attr)

    return new_graph



def edge_line_graph(
    G: nx.MultiDiGraph,
    graph_attrs: list[str] = None
) -> nx.MultiDiGraph:
    """
    Constructs a MultiDiGraph H where each node represents an edge from G,
    and edges in H connect if the corresponding edges in G share a node.

    Parameters:
        G (nx.MultiDiGraph): The input graph.

    Returns:
        H (nx.MultiDiGraph): The line graph based on edge incidence.
    """
    H = nx.MultiDiGraph()

    # Copy specified graph-level attributes if provided
    if graph_attrs:
        H.graph = {k: copy.deepcopy(v) for k, v in G.graph.items() if k in graph_attrs}

    # Each edge in G becomes a node in H
    edge_nodes = list(G.edges(keys=True))
    edge_id_map = {}  # maps (u, v, k) → node ID in H

    for idx, (u, v, k) in enumerate(edge_nodes):
        edge_data = G[u][v][k]

        u_x = G.nodes[u]["x"]
        u_y = G.nodes[u]["y"]
        v_x = G.nodes[v]["x"]
        v_y = G.nodes[v]["y"]
        x = (u_x + v_x) / 2.0
        y = (u_y + v_y) / 2.0

        # Add a node in H, corresponding to the edge in G
        osmid = edge_data.get("osmid", None)
        length = edge_data.get("length", None)

        H.add_node(idx, osmid=osmid, length=length, x=x, y=y)
        edge_id_map[(u, v, k)] = idx

    # Create a mapping from vertex in G to incident edges
    incident_edges = defaultdict(set)
    for (u, v, k), h_node_id in edge_id_map.items():
        incident_edges[u].add(h_node_id)
        incident_edges[v].add(h_node_id)

    # Connect edges in H if they share a vertex in G
    for node_edges in incident_edges.values():
        edge_list = list(node_edges)
        for i in range(len(edge_list)):
            for j in range(i + 1, len(edge_list)):
                e1 = edge_list[i]
                e2 = edge_list[j]
                length = node_distance(H, e1, e2)
                H.add_edge(e1, e2, length=length)

    return H

def node_distance(G: nx.MultiDiGraph, u, v) -> float:
    lat1, lon1 = G.nodes[u]["x"], G.nodes[u]["y"]
    lat2, lon2 = G.nodes[v]["x"], G.nodes[v]["y"]
    R = 6371000  

    # Convert degrees to radians
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return R * c

