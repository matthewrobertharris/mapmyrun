import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from database.config import engine
from database.models import Base
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def create_database():
    """Create the database if it doesn't exist"""
    # Connect to PostgreSQL server
    conn = psycopg2.connect(
        dbname='postgres',
        user='postgres',
        password='postgres',
        host='localhost'
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    
    cursor = conn.cursor()
    
    try:
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'mapmyrun'")
        exists = cursor.fetchone()
        
        if not exists:
            cursor.execute('CREATE DATABASE mapmyrun')
            logging.info("Database 'mapmyrun' created successfully")
        else:
            logging.info("Database 'mapmyrun' already exists")
            
    except Exception as e:
        logging.error(f"Error creating database: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()

def create_postgis_extension():
    """Create PostGIS extension in the mapmyrun database"""
    # Connect to the mapmyrun database
    conn = psycopg2.connect(
        dbname='mapmyrun',
        user='postgres',
        password='postgres',
        host='localhost'
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    
    cursor = conn.cursor()
    
    try:
        # Create PostGIS extension if it doesn't exist
        cursor.execute("""
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS postgis_topology;
        """)
        logging.info("PostGIS extension created successfully")
    except Exception as e:
        logging.error(f"Error creating PostGIS extension: {str(e)}")
        raise
    finally:
        cursor.close()
        conn.close()

def init_tables():
    """Create all database tables"""
    try:
        Base.metadata.create_all(bind=engine)
        logging.info("Database tables created successfully")
    except Exception as e:
        logging.error(f"Error creating tables: {str(e)}")
        raise

def main():
    """Initialize the database and create tables"""
    try:
        create_database()
        create_postgis_extension()
        init_tables()
        logging.info("Database initialization completed successfully")
    except Exception as e:
        logging.error(f"Database initialization failed: {str(e)}")
        return False
    return True

if __name__ == "__main__":
    main() 