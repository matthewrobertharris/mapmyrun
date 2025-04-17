import osmnx as ox
import networkx as nx
import graph_utils as gu

address = "45 Queens Road, New Lambton, NSW, 2305, Australia"
# This was taken from Google Maps
y = -32.929295
x = 151.710816
location_point = [y, x]

# Note that the geocoder for OSM doesn't always have the specific address and so may only locate the road
#location_point = ox.geocoder.geocode(address)
#print(location_point)

# Get roads within 1 km
G = ox.graph_from_point(location_point, dist=1000, dist_type='network', network_type='drive')

# Plot or export
#print(G.nodes.data())
#print(G.edges.data())
#print(G.graph.items())
#ox.plot_graph(G)

# I'd really like to consolidate some nodes and edges here.
# Specifically, I think there is a combination of road name, one-way, node proximity and vertex degree which can help
# However, this is really just a quality issue, the algorithms will still give you the route to run

#graph = ox.graph_from_address(address, dist=500, dist_type='network', network_type='drive')

#print(list(graph.nodes.data()))
#print(list(graph.edges.data()))
#print(graph.graph.items())
# Treat the nearest node as the starting node, I think
#nearest_nodes = ox.distance.nearest_nodes(graph, x, y, return_dist=True)
#print(nearest_nodes)
#nearest_edges = ox.distance.nearest_edges(graph, x, y, return_dist=True)
#print(nearest_edges)

#print(graph.graph.items())



# Remove all the fluff around the OSM graph
simple_graph = gu.deep_copy_multidigraph(G, node_attrs=["x", "y"], edge_attrs=["osmid", "length"], graph_attrs=["crs"])

# TODO 
# Need to add in the starting location node

#ox.plot_graph(simple_graph)

# This is effectively the inverse of the other graph, whereby the edges on the original are now nodes on the line graph
line_graph = gu.edge_line_graph(simple_graph, graph_attrs=["crs"])

print(line_graph.nodes.data())
#print(line_graph.edges.data())
#print(line_graph.graph.items())
ox.plot_graph(line_graph)


# The next thing is to calculate the shortest paths from each node to every other node (both in terms of distance and actual route)
# With the shortest paths calculated, then some paths can begin to be created.  This is the critical algorithm
# Once there are paths, then these should be made into solutions (i.e. groups of paths).  
#   It is assumed all the solutions have complete coverage (even if this is just out and back paths to fill in missing nodes)
# The solutions should then be evaluated according to total distance (lower the better) and average distance
# The paths should then be convertable back to the maps




# The algorithm is something like.  Note, the other variations would be:
# * Find the closest unvisited node 
# * Find the node which has a path with the largest unvisited walk from the current node (even if this is 1).  This would take a few extra structures to maintain I think
# * There is also a mixture - start with the furthest node in your first step and then walk closely back in on the way back


#   Start at the starting node:
#       Find the furthest unvisited vertex (next)
#           If the distance to get there and to the starting node, is less than the distance remaining
#           if(curr_next_dist + next_home_dist <= max_dist - curr_dist)
#               Go to this vertex (updating path)
#               Mark it has visited (globally)
#               Update the curr_dist and curr node
#               recursively call find_furthest_unvisisted_node(curr)
#           otherwise
#               return home (updating path)
#               Store path
#               Start new path, from home, but keeping the visited nodes
#           