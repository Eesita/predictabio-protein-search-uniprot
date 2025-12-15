#!/usr/bin/env python3
"""
Filter script to filter metadata DataFrames based on growth factor regex patterns.
"""

import pandas as pd
import re
from typing import List, Tuple


# Hardcoded growth factors with regex patterns
# Format: (growth_factor_name, regex_pattern)
GROWTH_FACTORS: List[Tuple[str, str]] = [
    
    # Bone Morphogenetic Protein 2
    # ("Bone Morphogenetic Protein 2 (BMP-2)", r'(?:[Bb]one[\s-]*[Mm]orphogenetic[\s-]*[Pp]rotein[\s-]*2|BMP[\s-]?2|hBMP[\s-]?2|rhBMP[\s-]?2|ErhBMP[\s-]?2|E\.BMP[\s-]?2)'),

    # Insulin-like Growth Factor 1
    ("Insulin-like Growth Factor 1 (IGF-1)", r'(?:[Ii]nsulin[\s-]*like[\s-]*[Gg]rowth[\s-]*[Ff]actor[\s-]*1|IGF[\s-]?1|IGF1|rhIGF[\s-]?1|hIGF[\s-]?1)'),

]


def filter_growth_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter DataFrame based on growth factor regex patterns.
    
    Args:
        df: Input DataFrame with 'title' and 'abstract' columns
        
    Returns:
        Filtered DataFrame with 'Growth_Factor_Name' column added
    """
    if df is None or df.empty:
        return df
    
    # Check for required columns
    if 'title' not in df.columns or 'abstract' not in df.columns:
        print(f"[filter1] Warning: Missing 'title' or 'abstract' columns. Available columns: {list(df.columns)}")
        return df
    
    # Create combined_text column
    df = df.copy()  # Avoid modifying original
    df["combined_text"] = df["title"].fillna("") + " " + df["abstract"].fillna("")
    
    # Collect all filtered results
    filtered_results = []
    
    # Loop through each growth factor
    for gf_name, gf_pattern in GROWTH_FACTORS:
        try:
            # Filter rows containing the regex pattern
            pattern = rf'{gf_pattern}'
            mask = df["combined_text"].str.contains(pattern, case=False, regex=True, na=False)
            filtered_df = df[mask].copy()
            
            if not filtered_df.empty:
                # Add growth factor name column
                filtered_df["Growth_Factor_Name"] = gf_name
                filtered_results.append(filtered_df)
                print(f"[filter1] Found {len(filtered_df)} articles matching '{gf_name}'")
        except re.error as e:
            print(f"[filter1] Regex error for growth factor '{gf_name}': {e}")
            continue
        except Exception as e:
            print(f"[filter1] Error processing growth factor '{gf_name}': {e}")
            continue
    
    # Combine all filtered results
    if filtered_results:
        result_df = pd.concat(filtered_results, ignore_index=True)
        # Drop combined_text column before returning
        result_df = result_df.drop(columns=['combined_text'], errors='ignore')
        print(f"[filter1] Total filtered articles: {len(result_df)} (from {len(df)} original)")
        return result_df
    else:
        print(f"[filter1] No articles matched any growth factor patterns")
        # Return empty DataFrame with same structure
        df = df.drop(columns=['combined_text'], errors='ignore')
        return df.iloc[0:0].copy()  # Empty DataFrame with same columns

