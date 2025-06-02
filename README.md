# Road Edge Cover Solution Generator

This Python program generates an edge cover solution for all roads within a specified distance from a given address. It visualizes the solution on an interactive map.

## Requirements

- Python 3.7+
- Required packages listed in `requirements.txt`

## Installation

1. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install required packages:
```bash
pip install -r requirements.txt
```

## Usage

Run the program:
```bash
python road_edge_cover.py
```

The program will prompt you for:
1. A starting address
2. A distance in meters

It will then:
1. Generate a road network around the specified location
2. Calculate an edge cover solution that covers all roads
3. Create an interactive map visualization saved as 'route_map.html'

## Output

The program generates a 'route_map.html' file that shows:
- The complete route (in red)
- The starting/ending point (green marker)
- An interactive map that you can zoom and pan 