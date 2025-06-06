from sqlalchemy import text, insert, func, and_
from geoalchemy2.shape import from_shape, to_shape
from geoalchemy2.functions import ST_Intersects, ST_Length, ST_LineSubstring
from shapely.geometry import Point, LineString, mapping
from shapely.ops import linemerge
from .models import User, Location, Route, GPSPoint, RoadSegment, Activity, UserRoadSegment, route_segments
from .config import engine, Base
from datetime import datetime
import logging

def init_db():
    """Initialize the database by creating all tables"""
    Base.metadata.create_all(bind=engine)

def create_user(db, username):
    """
    Create a new user
    
    Args:
        db: SQLAlchemy session
        username: Unique username for the new user
    """
    user = User(username=username)
    db.add(user)
    db.commit()
    return user

def get_user_by_username(db, username):
    """Get a user by their username"""
    return db.query(User).filter(User.username == username).first()

def create_location(db, user_id, name, address, latitude, longitude, max_distance):
    """
    Create a new location for a user
    
    Args:
        db: SQLAlchemy session
        user_id: ID of the user who owns this location
        name: Name of the location (e.g., "Home", "Work")
        address: Full address of the location
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        max_distance: Maximum distance in meters for routes from this location
    """
    point = Point(longitude, latitude)  # Note: PostGIS expects (lon, lat) order
    location = Location(
        user_id=user_id,
        name=name,
        address=address,
        latitude=latitude,
        longitude=longitude,
        max_distance=max_distance,
        point=from_shape(point)
    )
    db.add(location)
    db.commit()
    return location

def get_user_locations(db, user_id):
    """Get all locations for a specific user"""
    return db.query(Location).filter(Location.user_id == user_id).all()

def get_location_by_id(db, location_id):
    """Get a location by its ID"""
    return db.query(Location).filter(Location.id == location_id).first()

def add_route(db, location_id, name, description, points, distance=None, elevation_gain=None):
    """
    Add a new route with its GPS points to the database
    
    Args:
        db: SQLAlchemy session
        location_id: ID of the location this route belongs to
        name: Route name
        description: Route description
        points: List of (lat, lon, elevation, timestamp) tuples
        distance: Route distance in meters
        elevation_gain: Route elevation gain in meters
    """
    # Create LineString from points
    line_points = [(p[1], p[0]) for p in points]  # Convert to (lon, lat) for LineString
    line_string = LineString(line_points)
    
    # Create route
    route = Route(
        location_id=location_id,
        name=name,
        description=description,
        distance=distance,
        elevation_gain=elevation_gain,
        path=from_shape(line_string)
    )
    db.add(route)
    db.flush()  # Get route ID
    
    # Add GPS points
    for lat, lon, elevation, timestamp in points:
        point = GPSPoint(
            route_id=route.id,
            latitude=lat,
            longitude=lon,
            elevation=elevation,
            timestamp=timestamp,
            location=from_shape(Point(lon, lat))
        )
        db.add(point)
    
    db.commit()
    return route

def add_road_segment(db, osm_id, segment_id, node_u, node_v, name, road_type, coordinates, length=None, classification=None):
    """
    Add a new road segment to the database
    
    Args:
        db: SQLAlchemy session
        osm_id: Original OpenStreetMap ID
        segment_id: Unique segment identifier (osm_id_node_u_node_v)
        node_u: Start node ID
        node_v: End node ID
        name: Street name
        road_type: Type of road
        coordinates: List of (lon, lat) tuples
        length: Segment length in meters
        classification: Road classification
    """
    line_string = LineString(coordinates)
    
    segment = RoadSegment(
        osm_id=osm_id,
        segment_id=segment_id,
        node_u=node_u,
        node_v=node_v,
        name=name,
        road_type=road_type,
        length=length,
        classification=classification,
        geometry=from_shape(line_string)
    )
    db.add(segment)
    db.commit()
    return segment

def create_route_with_segments(db, location_id, name, description, segment_ids, segment_directions):
    """
    Create a new route from existing road segments
    
    Args:
        db: SQLAlchemy session
        location_id: ID of the location this route belongs to
        name: Route name
        description: Route description
        segment_ids: List of road segment IDs in order (using segment_id field)
        segment_directions: List of booleans indicating direction for each segment (True=forward, False=reverse)
    """
    # Create the route
    route = Route(
        location_id=location_id,
        name=name,
        description=description
    )
    db.add(route)
    db.flush()  # Get route ID
    
    # Add segments to route with order and direction
    for order, (segment_id, direction) in enumerate(zip(segment_ids, segment_directions)):
        stmt = insert(route_segments).values(
            route_id=route.id,
            segment_id=segment_id,
            segment_order=order,
            direction=direction
        )
        db.execute(stmt)
    
    # Calculate total distance properly handling repeated segments
    total_distance = 0
    for segment_id in segment_ids:
        segment = db.query(RoadSegment).filter(RoadSegment.segment_id == segment_id).first()
        if segment and segment.length:
            total_distance += segment.length
    
    route.distance = total_distance
    
    db.commit()
    return route

def get_route_segments(db, route_id):
    """
    Get all road segments for a route in order
    Returns list of tuples: (segment, direction)
    """
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        return []
    
    # Get segments with their order and direction
    segments_with_info = (
        db.query(RoadSegment, route_segments.c.direction)
        .join(route_segments)
        .filter(route_segments.c.route_id == route_id)
        .order_by(route_segments.c.segment_order)
        .all()
    )
    
    return segments_with_info

def get_location_routes(db, location_id):
    """Get all routes for a specific location"""
    return db.query(Route).filter(Route.location_id == location_id).all()

def get_location_segments(db, location_id):
    """Get all road segments for a specific location"""
    return db.query(RoadSegment).filter(RoadSegment.location_id == location_id).all()

def get_location_completed_segments(db, location_id):
    """Get all completed road segments for a specific location"""
    return db.query(RoadSegment).filter(
        RoadSegment.location_id == location_id,
        RoadSegment.is_completed == True
    ).all()

def get_location_uncompleted_segments(db, location_id):
    """Get all uncompleted road segments for a specific location"""
    return db.query(RoadSegment).filter(
        RoadSegment.location_id == location_id,
        RoadSegment.is_completed == False
    ).all()

def mark_segment_completed(db, segment_osm_id):
    """Mark a road segment as completed"""
    segment = db.query(RoadSegment).filter(RoadSegment.osm_id == segment_osm_id).first()
    if segment:
        segment.is_completed = True
        db.commit()
    return segment

def add_segments_to_route(db, route_id, segment_osm_ids, segment_directions):
    """
    Add road segments to an existing route
    
    Args:
        db: SQLAlchemy session
        route_id: ID of the route
        segment_osm_ids: List of road segment OSM IDs to add
        segment_directions: List of booleans indicating direction for each segment
    """
    # Get current highest order
    max_order = db.query(func.max(route_segments.c.segment_order))\
        .filter(route_segments.c.route_id == route_id)\
        .scalar() or -1
    
    # Add new segments with incremented order
    for i, (segment_osm_id, direction) in enumerate(zip(segment_osm_ids, segment_directions)):
        stmt = insert(route_segments).values(
            route_id=route_id,
            segment_osm_id=segment_osm_id,
            segment_order=max_order + 1 + i,
            direction=direction
        )
        db.execute(stmt)
    
    # Update route distance
    route = db.query(Route).filter(Route.id == route_id).first()
    if route:
        segments = db.query(RoadSegment).filter(RoadSegment.osm_id.in_(segment_osm_ids)).all()
        route.distance = (route.distance or 0) + sum(segment.length for segment in segments)
    
    db.commit()

def create_activity(db, user_id, strava_id, name, activity_type, start_time, gps_points_data,
                   distance=None, duration=None, elevation_gain=None, average_speed=None):
    """
    Create a new activity with GPS points from Strava data
    
    Args:
        db: SQLAlchemy session
        user_id: ID of the user who owns this activity
        strava_id: Strava's activity ID
        name: Activity name
        activity_type: Type of activity (e.g., 'Run', 'Ride')
        start_time: Start time of the activity
        gps_points_data: List of dicts containing GPS point data
            Each dict should have: latitude, longitude, elevation, timestamp,
            and optionally: distance, heart_rate, cadence, speed
        distance: Total distance in meters
        duration: Total duration in seconds
        elevation_gain: Total elevation gain in meters
        average_speed: Average speed in meters per second
    """
    # Create LineString from GPS points for the activity path
    line_points = [(p['longitude'], p['latitude']) for p in gps_points_data]
    line_string = LineString(line_points)
    
    # Create activity
    activity = Activity(
        user_id=user_id,
        strava_id=strava_id,
        name=name,
        activity_type=activity_type,
        start_time=start_time,
        distance=distance,
        duration=duration,
        elevation_gain=elevation_gain,
        average_speed=average_speed,
        path=from_shape(line_string)
    )
    db.add(activity)
    db.flush()  # Get activity ID
    
    # Add GPS points
    for point_data in gps_points_data:
        point = GPSPoint(
            activity_id=activity.id,
            latitude=point_data['latitude'],
            longitude=point_data['longitude'],
            elevation=point_data.get('elevation'),
            timestamp=point_data['timestamp'],
            distance=point_data.get('distance'),
            heart_rate=point_data.get('heart_rate'),
            cadence=point_data.get('cadence'),
            speed=point_data.get('speed'),
            location=from_shape(Point(point_data['longitude'], point_data['latitude']))
        )
        db.add(point)
    
    db.commit()
    return activity

def get_user_activities(db, user_id):
    """Get all activities for a specific user"""
    return db.query(Activity).filter(Activity.user_id == user_id).order_by(Activity.start_time.desc()).all()

def get_activity_by_strava_id(db, strava_id):
    """Get an activity by its Strava ID"""
    return db.query(Activity).filter(Activity.strava_id == strava_id).first()

def get_activity_by_id(db, activity_id):
    """Get an activity by its ID"""
    return db.query(Activity).filter(Activity.id == activity_id).first()

def get_activity_gps_points(db, activity_id):
    """Get all GPS points for an activity in chronological order"""
    return db.query(GPSPoint)\
        .filter(GPSPoint.activity_id == activity_id)\
        .order_by(GPSPoint.timestamp)\
        .all()

def get_user_activities_in_timerange(db, user_id, start_time, end_time):
    """Get all activities for a user within a specific time range"""
    return db.query(Activity)\
        .filter(Activity.user_id == user_id)\
        .filter(Activity.start_time >= start_time)\
        .filter(Activity.start_time <= end_time)\
        .order_by(Activity.start_time.desc())\
        .all()

def get_user_activities_by_type(db, user_id, activity_type):
    """Get all activities of a specific type for a user"""
    return db.query(Activity)\
        .filter(Activity.user_id == user_id)\
        .filter(Activity.activity_type == activity_type)\
        .order_by(Activity.start_time.desc())\
        .all()

def update_activity_stats(db, activity_id):
    """Update activity statistics based on GPS points"""
    activity = get_activity_by_id(db, activity_id)
    if not activity:
        return None
    
    points = get_activity_gps_points(db, activity_id)
    if not points:
        return activity
    
    # Update duration
    if points[0].timestamp and points[-1].timestamp:
        activity.duration = (points[-1].timestamp - points[0].timestamp).total_seconds()
    
    # Update distance if not already set
    if not activity.distance and points[-1].distance:
        activity.distance = points[-1].distance
    
    # Calculate elevation gain
    elevation_gains = []
    for i in range(1, len(points)):
        if points[i].elevation and points[i-1].elevation:
            gain = max(0, points[i].elevation - points[i-1].elevation)
            elevation_gains.append(gain)
    if elevation_gains:
        activity.elevation_gain = sum(elevation_gains)
    
    # Calculate average speed
    if activity.distance and activity.duration:
        activity.average_speed = activity.distance / activity.duration
    
    db.commit()
    return activity

def sync_user_road_segments(db, user_id):
    """
    Synchronize user's road segments based on their locations and routes.
    This ensures UserRoadSegment table has all segments from user's locations/routes.
    """
    # Get all road segments from user's locations through routes
    location_segments = (
        db.query(RoadSegment)
        .join(route_segments)
        .join(Route)
        .join(Location)
        .filter(Location.user_id == user_id)
        .distinct()
        .all()
    )
    
    # For each segment, create or update UserRoadSegment
    for segment in location_segments:
        user_segment = (
            db.query(UserRoadSegment)
            .filter(
                UserRoadSegment.user_id == user_id,
                UserRoadSegment.segment_id == segment.segment_id
            )
            .first()
        )
        
        if not user_segment:
            user_segment = UserRoadSegment(
                user_id=user_id,
                segment_id=segment.segment_id,
                name=segment.name,
                road_type=segment.road_type,
                length=segment.length,
                classification=segment.classification,
                geometry=segment.geometry
            )
            db.add(user_segment)
    
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error syncing user road segments: {str(e)}")

def update_segment_run_status(db, user_id, activity_id):
    """
    Update road segments' run status based on a specific activity.
    This should be called whenever a new activity is added.
    """
    activity = get_activity_by_id(db, activity_id)
    if not activity:
        return
    
    # Get activity path as LineString
    activity_line = to_shape(activity.path)
    
    # Get all user's road segments that haven't been run yet
    unrun_segments = (
        db.query(UserRoadSegment)
        .filter(
            UserRoadSegment.user_id == user_id,
            UserRoadSegment.has_been_run == False
        )
        .all()
    )
    
    # Check each segment for intersection with activity path
    for segment in unrun_segments:
        segment_line = to_shape(segment.geometry)
        
        # If activity path intersects with segment
        if activity_line.intersects(segment_line):
            # Calculate what portion of the segment was covered
            intersection = activity_line.intersection(segment_line)
            if isinstance(intersection, (LineString, MultiLineString)):
                coverage = intersection.length / segment_line.length
                # If significant portion was covered (e.g., >80%)
                if coverage > 0.8:
                    segment.has_been_run = True
                    segment.first_run_activity_id = activity_id
                    segment.first_run_timestamp = activity.start_time
                    segment.last_updated = datetime.utcnow()
    
    db.commit()

def get_user_road_segments(db, user_id, run_status=None):
    """
    Get user's road segments, optionally filtered by run status
    
    Args:
        db: SQLAlchemy session
        user_id: ID of the user
        run_status: Optional boolean to filter by has_been_run status
    """
    query = db.query(UserRoadSegment).filter(UserRoadSegment.user_id == user_id)
    
    if run_status is not None:
        query = query.filter(UserRoadSegment.has_been_run == run_status)
    
    return query.all()

def get_user_segment_stats(db, user_id):
    """Get statistics about user's road segments"""
    total_segments = (
        db.query(func.count(UserRoadSegment.id))
        .filter(UserRoadSegment.user_id == user_id)
        .scalar()
    )
    
    run_segments = (
        db.query(func.count(UserRoadSegment.id))
        .filter(
            UserRoadSegment.user_id == user_id,
            UserRoadSegment.has_been_run == True
        )
        .scalar()
    )
    
    total_length = (
        db.query(func.sum(UserRoadSegment.length))
        .filter(UserRoadSegment.user_id == user_id)
        .scalar() or 0
    )
    
    run_length = (
        db.query(func.sum(UserRoadSegment.length))
        .filter(
            UserRoadSegment.user_id == user_id,
            UserRoadSegment.has_been_run == True
        )
        .scalar() or 0
    )
    
    return {
        'total_segments': total_segments,
        'run_segments': run_segments,
        'total_length': total_length,
        'run_length': run_length,
        'completion_percentage': (run_length / total_length * 100) if total_length > 0 else 0
    }

def reset_segment_run_status(db, user_id, segment_id):
    """Reset the run status of a specific segment"""
    segment = (
        db.query(UserRoadSegment)
        .filter(
            UserRoadSegment.user_id == user_id,
            UserRoadSegment.segment_id == segment_id
        )
        .first()
    )
    
    if segment:
        segment.has_been_run = False
        segment.first_run_activity_id = None
        segment.first_run_timestamp = None
        segment.last_updated = datetime.utcnow()
        db.commit()
    
    return segment

def remove_location(db, location_id):
    """
    Remove a location and its associated routes.
    
    Args:
        db: SQLAlchemy session
        location_id: ID of the location to remove
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Get the location
        location = db.query(Location).filter(Location.id == location_id).first()
        if not location:
            return False
            
        # Get all routes for this location
        routes = db.query(Route).filter(Route.location_id == location_id).all()
        
        # Delete route segments first
        for route in routes:
            # Delete entries from route_segments association table
            db.execute(
                route_segments.delete().where(route_segments.c.route_id == route.id)
            )
        
        # Now delete the routes
        db.query(Route).filter(Route.location_id == location_id).delete()
        
        # Finally delete the location
        db.delete(location)
        
        db.commit()
        return True
        
    except Exception as e:
        db.rollback()
        logging.error(f"Error removing location: {str(e)}")
        return False

def clear_database(db):
    """
    Clear all data from the database by dropping and recreating all tables.
    WARNING: This will permanently delete all data!
    
    Args:
        db: SQLAlchemy session
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Close the current session
        db.close()
        
        # Drop all tables
        Base.metadata.drop_all(bind=engine)
        
        # Recreate all tables
        Base.metadata.create_all(bind=engine)
        
        return True
        
    except Exception as e:
        logging.error(f"Error clearing database: {str(e)}")
        return False 