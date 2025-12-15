#!/usr/bin/env python3
"""
Database utility module for PostgreSQL operations.
Handles connection management and bulk inserts with column alignment.
"""

import os
import re
import logging
from typing import Optional
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

def get_db_connection():
    """
    Get PostgreSQL connection from environment variables.
    
    Returns:
        psycopg2 connection object
        
    Raises:
        Exception: If connection fails
    """
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB")
    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    
    if not all([host, port, database, user, password]):
        raise ValueError("Missing required PostgreSQL connection parameters in environment variables")
    
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=host,
            port=int(port),
            database=database,
            user=user,
            password=password
        )
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        raise


def get_table_columns(conn, table_name: str) -> list:
    """
    Get list of column names from PostgreSQL table.
    
    Args:
        conn: PostgreSQL connection
        table_name: Name of the table
        
    Returns:
        List of column names in order
    """
    try:
        cursor = conn.cursor()
        query = """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s
            ORDER BY ordinal_position
        """
        cursor.execute(query, (table_name,))
        columns = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return columns
    except Exception as e:
        logger.error(f"Failed to get table columns for {table_name}: {e}")
        raise


def insert_metadata_bulk(df: pd.DataFrame, table_name: str, conn) -> int:
    """
    Insert DataFrame into PostgreSQL table with column alignment.
    
    Handles missing columns by:
    - Skipping DataFrame columns that don't exist in table
    - Adding NULL values for table columns not in DataFrame
    - Never skips rows - inserts all rows from DataFrame
    
    Args:
        df: DataFrame to insert
        table_name: Target table name
        conn: PostgreSQL connection
        
    Returns:
        Number of rows inserted
    """
    if df is None or df.empty:
        logger.warning(f"DataFrame is empty, nothing to insert into {table_name}")
        return 0
    
    try:
        # Get table columns from database
        table_columns = get_table_columns(conn, table_name)
        
        if not table_columns:
            raise ValueError(f"Table {table_name} not found or has no columns")
        
        # Create a copy of DataFrame to avoid modifying original
        df_aligned = df.copy()
        
        # Keep only columns that exist in both DataFrame and table
        # Create case-insensitive mapping: lowercase column name -> actual table column name
        table_columns_lower_map = {col.lower(): col for col in table_columns}
        df_columns = set(df_aligned.columns)
        df_columns_lower = {col.lower(): col for col in df_aligned.columns}
        
        # Rename DataFrame columns to match table column names (case-insensitive)
        rename_map = {}
        for df_col in df_aligned.columns:
            df_col_lower = df_col.lower()
            if df_col_lower in table_columns_lower_map:
                table_col = table_columns_lower_map[df_col_lower]
                if df_col != table_col:
                    rename_map[df_col] = table_col
                    logger.debug(f"[insert_metadata_bulk] Will rename DataFrame column '{df_col}' to '{table_col}' to match table")
        
        if rename_map:
            df_aligned = df_aligned.rename(columns=rename_map)
        
        # Find columns to keep (exist in both) - after renaming
        columns_to_keep = [col for col in df_aligned.columns if col.lower() in table_columns_lower_map]
        
        # Find columns missing from DataFrame (exist in table but not in DataFrame)
        # Use case-insensitive comparison
        missing_columns = []
        for table_col in table_columns:
            table_col_lower = table_col.lower()
            if table_col_lower not in {col.lower() for col in df_aligned.columns}:
                missing_columns.append(table_col)
        
        # Find columns to skip (exist in DataFrame but not in table) - case-insensitive
        columns_to_skip = []
        for df_col in df_aligned.columns:
            df_col_lower = df_col.lower()
            if df_col_lower not in table_columns_lower_map:
                columns_to_skip.append(df_col)
        
        if columns_to_skip:
            logger.info(f"[insert_metadata_bulk] Skipping {len(columns_to_skip)} columns not in table {table_name}: {columns_to_skip[:5]}...")
        
        # Keep only columns that exist in table
        df_aligned = df_aligned[columns_to_keep]
        
        # Convert sets, lists, and dicts appropriately for database insertion
        # - JSONB columns: Convert lists/dicts to JSON strings (pandas.to_sql converts lists to arrays, not JSONB)
        # - TEXT columns: Convert sets/lists to string representation
        # - Other columns: Convert sets to lists
        import json
        
        # Known JSONB columns (from schema) - these need JSON strings
        jsonb_columns = {'links', 'original_indices', 'enriched_fields', 'original_sources', 'all_dois', 'all_pmids'}
        
        for col in df_aligned.columns:
            if df_aligned[col].dtype == 'object':
                # Apply conversion function to entire column
                def convert_value(val):
                    # Handle None first
                    if val is None:
                        return None
                    
                    # Handle NaN - check if it's a scalar first to avoid array ambiguity
                    try:
                        # For scalars, use pd.isna directly
                        if not hasattr(val, '__iter__') or isinstance(val, (str, bytes)):
                            if pd.isna(val):
                                return None
                    except (ValueError, TypeError):
                        # If pd.isna fails, continue
                        pass
                    
                    # Handle numpy arrays and pandas Series - convert to list first
                    if hasattr(val, '__iter__') and not isinstance(val, (str, bytes)):
                        # Check if it's a numpy array or pandas Series
                        if hasattr(val, 'tolist'):
                            try:
                                val = val.tolist()
                            except:
                                val = list(val) if not isinstance(val, str) else val
                        elif isinstance(val, (list, tuple)):
                            val = list(val)
                    
                    # Handle sets - convert to list first
                    if isinstance(val, set):
                        val = list(val)
                    
                    # For JSONB columns: convert lists/dicts to JSON strings
                    if col in jsonb_columns:
                        if isinstance(val, list):
                            try:
                                return json.dumps(val)
                            except (TypeError, ValueError):
                                return str(val)
                        elif isinstance(val, dict):
                            try:
                                return json.dumps(val)
                            except (TypeError, ValueError):
                                return str(val)
                        # If it's already a JSON string, keep it
                        elif isinstance(val, str):
                            # Check if it's already valid JSON
                            try:
                                json.loads(val)  # Validate it's JSON
                                return val  # Already a JSON string
                            except (ValueError, TypeError):
                                return val  # Not JSON, but keep as string
                        return val
                    else:
                        # For non-JSONB columns (TEXT, etc.)
                        if isinstance(val, list):
                            # Convert list to string representation for TEXT columns
                            # This handles cases like authors_clean which is a set converted to list
                            try:
                                return str(val)
                            except:
                                return str(val)
                        elif isinstance(val, dict):
                            # Convert dict to string representation
                            try:
                                return json.dumps(val)  # Use JSON format for dicts
                            except:
                                return str(val)
                        return val
                
                df_aligned[col] = df_aligned[col].apply(convert_value)
        
        # Add NULL columns for missing table columns
        for col in missing_columns:
            df_aligned[col] = None
            logger.debug(f"[insert_metadata_bulk] Added NULL column: {col}")
        
        # Ensure column order matches table schema
        df_aligned = df_aligned[table_columns]
        
        # Create SQLAlchemy engine from connection
        # Extract connection string components
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_PORT", "5432")
        database = os.getenv("POSTGRES_DB")
        user = os.getenv("POSTGRES_USER")
        password = os.getenv("POSTGRES_PASSWORD")
        
        connection_string = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        engine = create_engine(connection_string)
        
        # Insert using pandas to_sql with append mode
        rows_inserted = df_aligned.to_sql(
            table_name,
            engine,
            if_exists='append',
            index=False,
            method='multi'  # For bulk insert
        )
        
        logger.info(f"[insert_metadata_bulk] Successfully inserted {rows_inserted} rows into {table_name}")
        return rows_inserted
        
    except Exception as e:
        logger.error(f"[insert_metadata_bulk] Error inserting into {table_name}: {e}")
        raise


def update_pdf_found_from_verified_csv(verified_csv_path: str, conn) -> int:
    """
    Update pdf_found column in metadata_final_filtered table based on verified CSV.
    
    Matches rows by content_uuid (or id if content_uuid not available) and updates pdf_found.
    
    Args:
        verified_csv_path: Path to verified CSV file from PDF downloader API
        conn: PostgreSQL connection
        
    Returns:
        Number of rows updated
    """
    if not os.path.exists(verified_csv_path):
        logger.error(f"[update_pdf_found] Verified CSV file not found: {verified_csv_path}")
        return 0
    
    try:
        # Read verified CSV
        verified_df = pd.read_csv(verified_csv_path)
        logger.info(f"[update_pdf_found] Read {len(verified_df)} rows from verified CSV")
        
        if verified_df.empty:
            logger.warning("[update_pdf_found] Verified CSV is empty")
            return 0
        
        # Determine matching column (prefer content_uuid, fallback to id)
        match_column = None
        if 'content_uuid' in verified_df.columns:
            match_column = 'content_uuid'
        elif 'id' in verified_df.columns:
            match_column = 'id'
        else:
            logger.error("[update_pdf_found] No matching column found (content_uuid or id)")
            return 0
        
        # Validate match_column is safe (only allow known values)
        if match_column not in ['content_uuid', 'id']:
            logger.error(f"[update_pdf_found] Invalid match column: {match_column}")
            return 0
        
        # Check if pdf found column exists in verified CSV
        pdf_found_col = None
        for col in ['pdf found', 'pdf_found', 'pdffound']:
            if col in verified_df.columns:
                pdf_found_col = col
                break
        
        if not pdf_found_col:
            logger.warning("[update_pdf_found] No 'pdf found' column found in verified CSV")
            return 0
        
        # Update pdf_found in metadata_final_filtered table
        cursor = conn.cursor()
        updated_count = 0
        
        # Build safe UPDATE query based on validated match_column
        if match_column == 'content_uuid':
            update_query = """
                UPDATE metadata_final_filtered 
                SET "pdf found" = %s
                WHERE content_uuid = %s
            """
        else:  # match_column == 'id'
            update_query = """
                UPDATE metadata_final_filtered 
                SET "pdf found" = %s
                WHERE id = %s
            """
        
        for _, row in verified_df.iterrows():
            match_value = row[match_column]
            
            # Skip if match value is null/empty
            if pd.isna(match_value) or match_value == '':
                continue
            
            pdf_found = row[pdf_found_col]
            
            # Convert boolean to proper format for PostgreSQL
            if isinstance(pdf_found, str):
                pdf_found = pdf_found.lower() in ['true', '1', 'yes']
            elif pd.isna(pdf_found):
                pdf_found = False
            else:
                pdf_found = bool(pdf_found)
            
            # Execute update with parameterized query
            cursor.execute(update_query, (pdf_found, str(match_value)))
            if cursor.rowcount > 0:
                updated_count += 1
        
        conn.commit()
        cursor.close()
        logger.info(f"[update_pdf_found] Updated {updated_count} rows in metadata_final_filtered")
        return updated_count
        
    except Exception as e:
        conn.rollback()
        logger.error(f"[update_pdf_found] Error updating pdf_found: {e}")
        raise


def extract_id_from_json_filename(json_filename: str) -> Optional[str]:
    """
    Extract id from GROBID JSON filename.
    
    Format: {id}.pdf_{timestamp}_all.json
    Example: 0b21504d-16e4-5951-a6b8-b8a0322b1fb1.pdf_20251204_023801_all.json
    Returns: 0b21504d-16e4-5951-a6b8-b8a0322b1fb1
    
    Args:
        json_filename: JSON filename from GROBID output
        
    Returns:
        Extracted id string, or None if extraction fails
    """
    if not json_filename:
        return None
    
    # Pattern: _YYYYMMDD_HHMMSS_all.json at the end
    # Remove this suffix to get {id}.pdf
    pattern = r'_(\d{8}_\d{6})_all\.json$'
    match = re.search(pattern, json_filename)
    
    if match:
        # Remove the matched suffix to get {id}.pdf
        pdf_name = json_filename[:match.start()]
        # Remove .pdf extension to get id
        if pdf_name.endswith('.pdf'):
            return pdf_name[:-4]  # Remove '.pdf'
    
    # Fallback: try to extract if pattern doesn't match exactly
    # Look for .pdf_ pattern followed by timestamp-like pattern
    if '.pdf_' in json_filename and '_all.json' in json_filename:
        parts = json_filename.split('.pdf_')
        if len(parts) == 2:
            return parts[0]  # This is the id
    
    logger.warning(f"[extract_id_from_json_filename] Could not extract id from: {json_filename}")
    return None


def update_methods_validation_from_csv(validation_csv_path: str, conn) -> int:
    """
    Update methods_validation_status in metadata_final_filtered table.
    
    Mapping:
    1. Read validation CSV (has 'file' column with JSON filenames)
    2. Extract id from JSON filename (format: {id}.pdf_{timestamp}_all.json)
    3. Match to database using id column
    4. Update methods_validation_status with classification answer
    
    Args:
        validation_csv_path: Path to methods classification summary CSV
        conn: PostgreSQL connection
        
    Returns:
        Number of rows updated
    """
    if not os.path.exists(validation_csv_path):
        logger.error(f"[update_methods_validation] Validation CSV file not found: {validation_csv_path}")
        return 0
    
    try:
        # Read validation CSV
        validation_df = pd.read_csv(validation_csv_path)
        logger.info(f"[update_methods_validation] Read {len(validation_df)} rows from validation CSV")
        
        if validation_df.empty:
            logger.warning("[update_methods_validation] Validation CSV is empty")
            return 0
        
        # Check required columns
        if 'file' not in validation_df.columns:
            logger.error("[update_methods_validation] 'file' column not found in validation CSV")
            return 0
        
        # Determine answer column name (could be 'answer', 'classification_answer', 'status', 'is_recombinant_production', etc.)
        answer_col = None
        for col in ['answer', 'classification_answer', 'status', 'classification', 'is_recombinant_production']:
            if col in validation_df.columns:
                answer_col = col
                break
        
        if not answer_col:
            logger.error("[update_methods_validation] No answer/status column found in validation CSV")
            logger.info(f"[update_methods_validation] Available columns: {list(validation_df.columns)}")
            return 0
        
        cursor = conn.cursor()
        updated_count = 0
        skipped_count = 0
        
        for _, row in validation_df.iterrows():
            json_filename = row['file']
            answer = row[answer_col]
            
            # Extract id from JSON filename
            paper_id = extract_id_from_json_filename(json_filename)
            
            if not paper_id:
                skipped_count += 1
                logger.debug(f"[update_methods_validation] Could not extract id from: {json_filename}")
                continue
            
            # Normalize answer value
            if pd.isna(answer) or answer == '':
                skipped_count += 1
                continue
            
            answer_str = str(answer).lower().strip()
            
            # Handle is_recombinant_production column (boolean/string values)
            if answer_col == 'is_recombinant_production':
                # First check if it's already a valid yes/no value
                if answer_str in ['yes', 'no']:
                    # Already valid, keep it as is
                    pass
                # Convert boolean/boolean-like values to yes/no
                elif answer in [True, 'True', 'true', '1', 1]:
                    answer_str = 'yes'
                elif answer in [False, 'False', 'false', '0', 0]:
                    answer_str = 'no'
                else:
                    # If value is unclear, treat as insufficient_data
                    answer_str = 'insufficient_data'
                    logger.debug(f"[update_methods_validation] Unclear is_recombinant_production value: {answer}, treating as insufficient_data")
            
            # Handle nested classification structure if answer_col is 'classification'
            if answer_col == 'classification' and isinstance(answer, str):
                # Try to parse as JSON or extract answer from string
                try:
                    import json
                    classification_dict = json.loads(answer)
                    answer_str = classification_dict.get('answer', '').lower().strip()
                except:
                    # If not JSON, try to extract from string
                    if 'answer' in answer.lower():
                        # Try to find answer value in the string
                        answer_match = re.search(r'"answer"\s*:\s*"([^"]+)"', answer)
                        if answer_match:
                            answer_str = answer_match.group(1).lower().strip()
            
            # Validate answer value
            if answer_str not in ['yes', 'no', 'error', 'insufficient_data']:
                skipped_count += 1
                logger.debug(f"[update_methods_validation] Invalid answer value: {answer_str} (from {answer})")
                continue
            
            # Update database
            # Check if methods_validation_status column exists
            try:
                update_query = """
                    UPDATE metadata_final_filtered 
                    SET methods_validation_status = %s
                    WHERE id = %s
                """
                
                logger.info(f"[update_methods_validation] Updating id={paper_id} with status={answer_str} (from {json_filename})")
                cursor.execute(update_query, (answer_str, paper_id))
                if cursor.rowcount > 0:
                    updated_count += 1
                    logger.info(f"[update_methods_validation] ✅ Successfully updated id={paper_id} with status={answer_str}")
                else:
                    skipped_count += 1
                    logger.warning(f"[update_methods_validation] ⚠️ No row found with id: {paper_id} (from {json_filename})")
            except Exception as db_error:
                # Check if column doesn't exist
                if 'column "methods_validation_status" does not exist' in str(db_error).lower():
                    logger.error("[update_methods_validation] Column 'methods_validation_status' does not exist in metadata_final_filtered table")
                    logger.error("[update_methods_validation] Please add the column: ALTER TABLE metadata_final_filtered ADD COLUMN methods_validation_status TEXT;")
                    conn.rollback()
                    raise
                else:
                    raise
        
        conn.commit()
        cursor.close()
        
        logger.info(f"[update_methods_validation] Updated {updated_count} rows, skipped {skipped_count} rows")
        return updated_count
        
    except Exception as e:
        conn.rollback()
        logger.error(f"[update_methods_validation] Error updating methods validation status: {e}")
        raise

