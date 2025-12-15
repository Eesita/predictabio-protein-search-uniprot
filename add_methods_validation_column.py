#!/usr/bin/env python3
"""
Script to add methods_validation_status column to metadata_final_filtered table.
Reads connection details from environment variables or .env file.
"""

import os
import sys
from pathlib import Path

# Try to load .env file manually if it exists
env_file = Path(__file__).parent / "client" / ".env"
if env_file.exists():
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key.strip()] = value.strip()

# Get PostgreSQL connection details
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")

if not all([POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD]):
    print("Error: Missing PostgreSQL connection details.")
    print("Please set the following environment variables:")
    print("  - POSTGRES_HOST (default: localhost)")
    print("  - POSTGRES_PORT (default: 5432)")
    print("  - POSTGRES_DB (required)")
    print("  - POSTGRES_USER (required)")
    print("  - POSTGRES_PASSWORD (required)")
    sys.exit(1)

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 not installed. Please install it:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

# SQL statements
ADD_COLUMN_SQL = """
    ALTER TABLE metadata_final_filtered 
    ADD COLUMN IF NOT EXISTS methods_validation_status TEXT;
"""

ADD_COMMENT_SQL = """
    COMMENT ON COLUMN metadata_final_filtered.methods_validation_status IS 
    'Methods validation status from methods_validator_node. Values: yes (passed), no (failed), error, insufficient_data, or NULL (not validated)';
"""

def main():
    print(f"Connecting to PostgreSQL at {POSTGRES_HOST}:{POSTGRES_PORT}...")
    
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=int(POSTGRES_PORT),
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            sslmode='prefer'
        )
        print(f"✓ Connected to database: {POSTGRES_DB}")
        
        cursor = conn.cursor()
        
        # Add column
        print("\nAdding methods_validation_status column...")
        cursor.execute(ADD_COLUMN_SQL)
        print("✓ Column added successfully")
        
        # Add comment (may fail if column already exists with different comment, that's okay)
        try:
            print("\nAdding column comment...")
            cursor.execute(ADD_COMMENT_SQL)
            print("✓ Comment added successfully")
        except Exception as e:
            print(f"⚠ Warning: Could not add comment (this is okay if column already exists): {e}")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n✅ Migration completed successfully!")
        print("\nColumn 'methods_validation_status' has been added to 'metadata_final_filtered' table.")
        print("Values: 'yes' (passed), 'no' (failed), 'error', 'insufficient_data', or NULL (not validated)")
        
    except psycopg2.Error as e:
        print(f"\n❌ Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

