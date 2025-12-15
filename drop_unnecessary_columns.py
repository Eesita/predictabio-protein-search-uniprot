#!/usr/bin/env python3
"""
Drop unnecessary columns from metadata tables to reduce memory usage.

This script executes the SQL migration to remove columns that are no longer needed:
- title_clean, authors_clean, abstract_clean, doi_clean, pmid_clean
- is_merged, merged_count, merge_type, original_indices, merge_timestamp
- match_type, match_value, enriched_fields, original_sources
- all_dois, all_pmids, deduplicated_at
"""

import os
import sys
import psycopg2
from pathlib import Path

# Load environment variables from .env file
def load_env():
    """Load environment variables from client/.env file"""
    env_path = Path(__file__).parent / "client" / ".env"
    if not env_path.exists():
        print(f"Error: .env file not found at {env_path}")
        sys.exit(1)
    
    env_vars = {}
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key.strip()] = value.strip()
    
    return env_vars

def get_db_connection():
    """Create PostgreSQL connection using environment variables"""
    env = load_env()
    
    host = env.get('POSTGRES_HOST', 'localhost')
    port = env.get('POSTGRES_PORT', '5432')
    database = env.get('POSTGRES_DB', 'predictabio')
    user = env.get('POSTGRES_USER', 'postgres')
    password = env.get('POSTGRES_PASSWORD', '')
    
    try:
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            sslmode='prefer'
        )
        print(f"✓ Connected to PostgreSQL: {database}@{host}:{port}")
        return conn
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        raise

def execute_sql_file(conn, sql_file_path):
    """Execute SQL commands from a file"""
    sql_file = Path(sql_file_path)
    if not sql_file.exists():
        print(f"Error: SQL file not found: {sql_file_path}")
        sys.exit(1)
    
    with open(sql_file, 'r') as f:
        sql_content = f.read()
    
    # Remove comments and split by semicolons
    lines = []
    for line in sql_content.split('\n'):
        # Remove inline comments
        if '--' in line:
            line = line[:line.index('--')]
        lines.append(line.strip())
    
    # Join lines and split by semicolons
    cleaned_content = ' '.join(lines)
    statements = [s.strip() for s in cleaned_content.split(';') if s.strip()]
    
    cursor = conn.cursor()
    executed_count = 0
    
    try:
        for statement in statements:
            if statement and not statement.startswith('SELECT'):  # Skip SELECT statements for now
                cursor.execute(statement)
                executed_count += 1
                print(f"  ✓ Executed: {statement[:50]}...")
        
        conn.commit()
        print(f"\n✓ Executed {executed_count} SQL statements successfully")
        cursor.close()
    except Exception as e:
        conn.rollback()
        cursor.close()
        print(f"✗ SQL execution failed: {e}")
        import traceback
        traceback.print_exc()
        raise

def main():
    print("=" * 80)
    print("Dropping unnecessary columns from metadata tables")
    print("=" * 80)
    
    # Get SQL file path
    sql_file = Path(__file__).parent / "drop_unnecessary_columns.sql"
    
    if not sql_file.exists():
        print(f"Error: SQL file not found: {sql_file}")
        sys.exit(1)
    
    # Connect to database
    conn = get_db_connection()
    
    try:
        # Execute migration
        print(f"\nExecuting SQL migration: {sql_file}")
        execute_sql_file(conn, sql_file)
        
        print("\n✓ Migration completed successfully!")
        print("\nRemaining columns:")
        
        # Show remaining columns
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                table_name,
                column_name,
                data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
                AND table_name IN (
                    'metadata_fetched',
                    'metadata_growth_factor_filtered',
                    'metadata_parameters_extracted',
                    'metadata_final_filtered'
                )
            ORDER BY table_name, ordinal_position;
        """)
        
        current_table = None
        for row in cursor.fetchall():
            table_name, column_name, data_type = row
            if table_name != current_table:
                if current_table is not None:
                    print()
                print(f"\n{table_name}:")
                current_table = table_name
            print(f"  - {column_name} ({data_type})")
        
        cursor.close()
        
    except Exception as e:
        print(f"\n✗ Migration failed: {e}")
        sys.exit(1)
    finally:
        conn.close()
        print("\n✓ Database connection closed")

if __name__ == "__main__":
    main()

