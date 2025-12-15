#!/usr/bin/env python3
"""
Script to delete all data from metadata pipeline tables.
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
    from psycopg2 import sql
except ImportError:
    print("Error: psycopg2 not installed. Please install it:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

# Tables to delete data from (in order to respect foreign key constraints)
# This list ensures proper deletion order, but we'll also discover all metadata_* tables
TABLES = [
    "metadata_final_filtered",
    "metadata_parameters_extracted",
    "metadata_growth_factor_filtered",
    "metadata_fetched",
]

def discover_metadata_tables(cursor):
    """Discover all metadata_* tables in the database"""
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name LIKE 'metadata_%'
        ORDER BY table_name
    """)
    discovered_tables = [row[0] for row in cursor.fetchall()]
    return discovered_tables

def main():
    print(f"Connecting to PostgreSQL database '{POSTGRES_DB}' at {POSTGRES_HOST}:{POSTGRES_PORT}...")
    
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=int(POSTGRES_PORT),
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        cursor = conn.cursor()
        
        print(f"✓ Connected successfully\n")
        
        # Discover all metadata tables
        discovered_tables = discover_metadata_tables(cursor)
        if discovered_tables:
            print(f"Discovered {len(discovered_tables)} metadata table(s):")
            for table in discovered_tables:
                print(f"  - {table}")
            print()
        
        # Use discovered tables, but maintain order from TABLES list for foreign key constraints
        # Add any discovered tables not in the list
        tables_to_delete = []
        for table in TABLES:
            if table in discovered_tables:
                tables_to_delete.append(table)
        # Add any additional discovered tables not in TABLES list
        for table in discovered_tables:
            if table not in tables_to_delete:
                tables_to_delete.append(table)
        
        if not tables_to_delete:
            print("⚠️  No metadata tables found in database.")
            cursor.close()
            conn.close()
            return
        
        # Get row counts before deletion
        print("Current row counts:")
        for table in tables_to_delete:
            try:
                cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
                count = cursor.fetchone()[0]
                print(f"  {table}: {count} rows")
            except Exception as e:
                print(f"  {table}: Error reading count - {e}")
        
        # Ask for confirmation (skip if --yes flag is provided)
        skip_confirmation = '--yes' in sys.argv or '-y' in sys.argv
        
        if not skip_confirmation:
            print("\n⚠️  WARNING: This will delete ALL data from the following tables:")
            for table in tables_to_delete:
                print(f"   - {table}")
            
            response = input("\nAre you sure you want to proceed? (yes/no): ").strip().lower()
            
            if response != 'yes':
                print("Deletion cancelled.")
                cursor.close()
                conn.close()
                return
        else:
            print("\n⚠️  WARNING: Deleting ALL data from the following tables:")
            for table in tables_to_delete:
                print(f"   - {table}")
            print("(Confirmation skipped due to --yes flag)")
        
        print("\nDeleting data...")
        
        # Delete data from each table
        total_deleted = 0
        failed_tables = []
        for table in tables_to_delete:
            try:
                # Use TRUNCATE for faster deletion
                cursor.execute(sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.Identifier(table)))
                print(f"  ✓ Deleted all rows from {table}")
                total_deleted += 1
            except Exception as e:
                print(f"  ✗ Error deleting from {table}: {e}")
                failed_tables.append(table)
        
        # Commit the transaction
        conn.commit()
        
        print(f"\n✓ Successfully deleted data from {total_deleted} table(s)")
        if failed_tables:
            print(f"  ⚠️  Failed to delete from {len(failed_tables)} table(s): {', '.join(failed_tables)}")
        
        # Verify deletion
        print("\nVerifying deletion (row counts should be 0):")
        all_empty = True
        for table in tables_to_delete:
            try:
                cursor.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
                count = cursor.fetchone()[0]
                status = "✓" if count == 0 else "✗"
                print(f"  {status} {table}: {count} rows")
                if count > 0:
                    all_empty = False
            except Exception as e:
                print(f"  ✗ {table}: Error verifying - {e}")
                all_empty = False
        
        if all_empty:
            print("\n✅ All tables successfully emptied")
        else:
            print("\n⚠️  Some tables still contain data")
        
        cursor.close()
        conn.close()
        print("\n✓ Database connection closed")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

