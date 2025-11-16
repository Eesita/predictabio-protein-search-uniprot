#!/usr/bin/env python3
"""
Final filtering: Human + E. coli + Growth Factor Pattern Match
Filters for human proteins expressed in E. coli that match growth factor regex patterns.
"""

import pandas as pd
import re
from typing import Dict, Optional
from scripts.filter1 import GROWTH_FACTORS


def filter_human_ecoli_gf(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter DataFrame for human proteins expressed in E. coli that match growth factor patterns.
    
    Filters:
    1. Q4_Species contains "Human" (case-insensitive)
    2. Q5_Host_Organism contains "E. coli" or "Escherichia coli" (case-insensitive)
    3. Q3_Recombinant_Proteins matches the regex pattern from Growth_Factor_Name
    
    Args:
        df: Input DataFrame with Q3_Recombinant_Proteins, Q4_Species, Q5_Host_Organism, Growth_Factor_Name columns
        
    Returns:
        Filtered DataFrame
    """
    if df is None or df.empty:
        return df
    
    # Check for required columns
    required_columns = ['Q3_Recombinant_Proteins', 'Q4_Species', 'Q5_Host_Organism', 'Growth_Factor_Name']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"[filter_human_ecoli] Warning: Missing required columns: {missing_columns}")
        print(f"[filter_human_ecoli] Available columns: {list(df.columns)}")
        return df
    
    # Create a mapping from Growth_Factor_Name to regex pattern
    gf_pattern_map: Dict[str, str] = {name: pattern for name, pattern in GROWTH_FACTORS}
    
    print(f"[filter_human_ecoli] Starting final filtering...")
    print(f"[filter_human_ecoli] Original row count: {len(df)}")
    
    # Filter 1: Q4_Species contains "Human" (case-insensitive, but exclude "non-human")
    # Match patterns like: "Human", "Human, ...", "... Human", etc.
    # But exclude: "non-human", "Animal (non-human)", "nonhuman", etc.
    human_pattern = r'^Human$|^Human\s|Human,|\bHuman\b'
    non_human_pattern = r'non-human|nonhuman|\(non-human\)'
    
    # Match Human but exclude non-human patterns
    human_mask = (
        df['Q4_Species'].str.contains(human_pattern, case=False, na=False, regex=True) &
        ~df['Q4_Species'].str.contains(non_human_pattern, case=False, na=False, regex=True)
    )
    df_human = df[human_mask].copy()
    print(f"[filter_human_ecoli] After Human species filter: {len(df_human)} rows")
    
    # Debug: show sample of filtered species if needed
    if len(df_human) > 0:
        sample_species = df_human['Q4_Species'].unique()[:5]
        print(f"[filter_human_ecoli] Sample species in filtered data: {list(sample_species)}")
    
    if df_human.empty:
        print(f"[filter_human_ecoli] No rows match Human species filter")
        return df_human
    
    # Filter 2: Q5_Host_Organism contains "E. coli" or "Escherichia coli" (case-insensitive)
    ecoli_pattern = r'\b(?:Escherichia|E\.)\s*coli\b'
    ecoli_mask = df_human['Q5_Host_Organism'].str.contains(ecoli_pattern, case=False, na=False, regex=True)
    df_ecoli = df_human[ecoli_mask].copy()
    print(f"[filter_human_ecoli] After E. coli host filter: {len(df_ecoli)} rows")
    
    if df_ecoli.empty:
        print(f"[filter_human_ecoli] No rows match E. coli host filter")
        return df_ecoli
    
    # Filter 3: Q3_Recombinant_Proteins matches regex pattern from Growth_Factor_Name
    def matches_gf_pattern(row) -> bool:
        """Check if Q3_Recombinant_Proteins matches the growth factor regex pattern."""
        gf_name = row.get('Growth_Factor_Name')
        q3_proteins = row.get('Q3_Recombinant_Proteins')
        
        # Skip if Growth_Factor_Name is missing or empty
        if pd.isna(gf_name) or not str(gf_name).strip():
            return False
        
        # Skip if Q3_Recombinant_Proteins is missing or empty
        if pd.isna(q3_proteins) or not str(q3_proteins).strip():
            return False
        
        # Get regex pattern for this growth factor
        gf_name_str = str(gf_name).strip()
        regex_pattern = gf_pattern_map.get(gf_name_str)
        if not regex_pattern:
            # Growth factor not found in map, skip this row
            return False
        
        # Check if Q3_Recombinant_Proteins contains the pattern (case-insensitive)
        try:
            # Use the regex pattern to search in Q3_Recombinant_Proteins
            # The pattern should match any part of the protein names listed in Q3
            q3_text = str(q3_proteins)
            matches = bool(re.search(regex_pattern, q3_text, re.IGNORECASE))
            return matches
        except re.error as e:
            print(f"[filter_human_ecoli] Regex error for growth factor '{gf_name}': {e}")
            return False
        except Exception as e:
            print(f"[filter_human_ecoli] Error checking pattern for growth factor '{gf_name}': {e}")
            return False
    
    # Apply growth factor pattern matching
    gf_pattern_mask = df_ecoli.apply(matches_gf_pattern, axis=1)
    df_final = df_ecoli[gf_pattern_mask].copy()
    print(f"[filter_human_ecoli] After growth factor pattern match filter: {len(df_final)} rows")
    
    print(f"[filter_human_ecoli] Final filtering complete: {len(df)} → {len(df_final)} rows")
    
    return df_final

