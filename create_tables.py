#!/usr/bin/env python3
"""
Script to create PostgreSQL tables for metadata pipeline.
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
    from psycopg2 import sql
except ImportError:
    print("Error: psycopg2 not installed. Please install it:")
    print("  pip install psycopg2-binary")
    sys.exit(1)

# SQL statements for table creation
CREATE_TABLES = {
    "metadata_fetched": """
        CREATE TABLE IF NOT EXISTS metadata_fetched (
            id TEXT,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            journal TEXT,
            "publication year" INTEGER,
            doi TEXT,
            pmid TEXT,
            citations INTEGER,
            "open access" BOOLEAN,
            source TEXT,
            links JSONB,
            fetched_at TIMESTAMP,
            "pubmed link" TEXT,
            "external download url" TEXT,
            content_uuid TEXT,
            accession TEXT
        )
    """,
    "metadata_growth_factor_filtered": """
        CREATE TABLE IF NOT EXISTS metadata_growth_factor_filtered (
            id TEXT,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            journal TEXT,
            "publication year" INTEGER,
            doi TEXT,
            pmid TEXT,
            citations INTEGER,
            "open access" BOOLEAN,
            source TEXT,
            links JSONB,
            fetched_at TIMESTAMP,
            "pubmed link" TEXT,
            "external download url" TEXT,
            content_uuid TEXT,
            Growth_Factor_Name TEXT,
            accession TEXT
        )
    """,
    "metadata_parameters_extracted": """
        CREATE TABLE IF NOT EXISTS metadata_parameters_extracted (
            id TEXT,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            journal TEXT,
            "publication year" INTEGER,
            doi TEXT,
            pmid TEXT,
            citations INTEGER,
            "open access" BOOLEAN,
            source TEXT,
            links JSONB,
            fetched_at TIMESTAMP,
            "pubmed link" TEXT,
            "external download url" TEXT,
            content_uuid TEXT,
            Growth_Factor_Name TEXT,
            Q1_Protein_Production TEXT,
            Q2_Reasoning TEXT,
            Q3_Recombinant_Proteins TEXT,
            Q4_Species TEXT,
            Q5_Host_Organism TEXT,
            accession TEXT
        )
    """,
    "metadata_final_filtered": """
        CREATE TABLE IF NOT EXISTS metadata_final_filtered (
            id TEXT,
            title TEXT,
            abstract TEXT,
            authors TEXT,
            journal TEXT,
            "publication year" INTEGER,
            doi TEXT,
            pmid TEXT,
            citations INTEGER,
            "open access" BOOLEAN,
            source TEXT,
            links JSONB,
            fetched_at TIMESTAMP,
            "pubmed link" TEXT,
            "external download url" TEXT,
            content_uuid TEXT,
            Growth_Factor_Name TEXT,
            Q1_Protein_Production TEXT,
            Q2_Reasoning TEXT,
            Q3_Recombinant_Proteins TEXT,
            Q4_Species TEXT,
            Q5_Host_Organism TEXT,
            "pdf found" BOOLEAN,
            accession TEXT
        )
    """
}

def main():
    print(f"Connecting to PostgreSQL...")
    print(f"  Host: {POSTGRES_HOST}")
    print(f"  Port: {POSTGRES_PORT}")
    print(f"  Database: {POSTGRES_DB}")
    print(f"  User: {POSTGRES_USER}")
    
    try:
        # Connect to PostgreSQL
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD
        )
        conn.autocommit = True
        cursor = conn.cursor()
        
        print("\nCreating tables...")
        
        # Create each table
        for table_name, create_sql in CREATE_TABLES.items():
            try:
                cursor.execute(create_sql)
                print(f"  ✓ Created table: {table_name}")
            except Exception as e:
                print(f"  ✗ Error creating table {table_name}: {e}")
                raise
        
        # Verify tables were created
        print("\nVerifying tables...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name LIKE 'metadata_%'
            ORDER BY table_name
        """)
        tables = cursor.fetchall()
        
        if tables:
            print("  Created tables:")
            for (table_name,) in tables:
                print(f"    - {table_name}")
        else:
            print("  Warning: No metadata tables found")
        
        cursor.close()
        conn.close()
        
        print("\n✓ All tables created successfully!")
        
    except psycopg2.OperationalError as e:
        print(f"\n✗ Connection error: {e}")
        print("\nPlease check:")
        print("  1. PostgreSQL is running")
        print("  2. Connection details are correct")
        print("  3. Database exists")
        print("  4. User has CREATE TABLE permissions")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

