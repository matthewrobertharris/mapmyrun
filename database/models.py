from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Boolean, Table, UniqueConstraint
from sqlalchemy.orm import relationship
from geoalchemy2 import Geometry
from .config import Base
from datetime import datetime

# Association table for Route-RoadSegment many-to-many relationship
route_segments = Table(
    'route_segments',
    Base.metadata,
    Column('route_id', Integer, ForeignKey('routes.id'), primary_key=True),
    Column('segment_osm_id', String, ForeignKey('road_segments.osm_id'), primary_key=True),
    Column('segment_order', Integer, nullable=False),  # Order of segments within the route
    Column('direction', Boolean, nullable=False)  # True for forward, False for reverse
)

class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    locations = relationship("Location", back_populates="user")
    activities = relationship("Activity", back_populates="user")
    road_segments = relationship("UserRoadSegment", back_populates="user")

class UserRoadSegment(Base):
    __tablename__ = 'user_road_segments'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    osm_id = Column(String, nullable=False)
    name = Column(String)
    road_type = Column(String)
    length = Column(Float)  # in meters
    has_been_run = Column(Boolean, default=False)
    first_run_activity_id = Column(Integer, ForeignKey('activities.id'))
    first_run_timestamp = Column(DateTime)
    
    # Geometry column for the road segment
    geometry = Column(Geometry('LINESTRING'))
    
    # Additional metadata
    classification = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="road_segments")
    first_run_activity = relationship("Activity")
    
    # Unique constraint to ensure one segment per user
    __table_args__ = (
        UniqueConstraint('user_id', 'osm_id', name='uix_user_segment'),
    )

class Activity(Base):
    __tablename__ = 'activities'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    strava_id = Column(String, unique=True)  # Strava's activity ID
    name = Column(String, nullable=False)
    activity_type = Column(String, nullable=False)  # e.g., 'Run', 'Ride'
    start_time = Column(DateTime, nullable=False)
    distance = Column(Float)  # in meters
    duration = Column(Float)  # in seconds
    elevation_gain = Column(Float)  # in meters
    average_speed = Column(Float)  # in meters per second
    
    # Geometry column for the activity path
    path = Column(Geometry('LINESTRING'))
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="activities")
    gps_points = relationship("GPSPoint", back_populates="activity", cascade="all, delete-orphan")

class GPSPoint(Base):
    __tablename__ = 'gps_points'

    id = Column(Integer, primary_key=True)
    activity_id = Column(Integer, ForeignKey('activities.id'), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    elevation = Column(Float)
    timestamp = Column(DateTime, nullable=False)
    distance = Column(Float)  # distance from start in meters
    heart_rate = Column(Integer)  # beats per minute
    cadence = Column(Integer)  # steps per minute
    speed = Column(Float)  # instantaneous speed in meters per second
    
    # Geometry column for the point
    location = Column(Geometry('POINT'))
    
    # Relationship with activity
    activity = relationship("Activity", back_populates="gps_points")

class Location(Base):
    __tablename__ = 'locations'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    max_distance = Column(Float, nullable=False)  # Maximum distance in meters
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Geometry column for the location point
    point = Column(Geometry('POINT'))
    
    # Relationships
    user = relationship("User", back_populates="locations")
    routes = relationship("Route", back_populates="location", cascade="all, delete-orphan")

class Route(Base):
    __tablename__ = 'routes'

    id = Column(Integer, primary_key=True)
    location_id = Column(Integer, ForeignKey('locations.id'), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    distance = Column(Float)  # in meters
    elevation_gain = Column(Float)  # in meters
    is_completed = Column(Boolean, default=False)
    
    # Geometry column for the route path
    path = Column(Geometry('LINESTRING'))
    
    # Relationships
    location = relationship("Location", back_populates="routes")
    road_segments = relationship(
        "RoadSegment",
        secondary=route_segments,
        order_by=route_segments.c.segment_order,
        back_populates="routes"
    )

class RoadSegment(Base):
    __tablename__ = 'road_segments'

    osm_id = Column(String, primary_key=True)
    name = Column(String)
    road_type = Column(String)
    length = Column(Float)  # in meters
    
    # Geometry column for the road segment
    geometry = Column(Geometry('LINESTRING'))
    
    # Additional metadata
    classification = Column(String)
    last_updated = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    routes = relationship(
        "Route",
        secondary=route_segments,
        back_populates="road_segments"
    ) 