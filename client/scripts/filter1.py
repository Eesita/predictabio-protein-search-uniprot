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
    # Insulin
    ("Insulin", r'\binsulin\b|INS\b'),
    
    # Insulin-like Growth Factors
    ("IGF-1", r'\bIGF-?1\b|insulin-like growth factor-?1\b|somatomedin-?C\b'),
    ("IGF-2", r'\bIGF-?2\b|insulin-like growth factor-?2\b|somatomedin-?A\b'),
    
    # Epidermal Growth Factor
    ("EGF", r'\bEGF\b|epidermal growth factor\b'),
    
    # Fibroblast Growth Factors
    ("FGF", r'\bFGF-?\d+\b|fibroblast growth factor-?\d*\b'),
    
    # Platelet-Derived Growth Factor
    ("PDGF", r'\bPDGF-?[AB]?\b|platelet-?derived growth factor\b'),
    
    # Vascular Endothelial Growth Factor
    ("VEGF", r'\bVEGF-?[A-Z]?\b|vascular endothelial growth factor\b'),
    
    # Nerve Growth Factor
    ("NGF", r'\bNGF\b|nerve growth factor\b'),
    
    # Transforming Growth Factor
    ("TGF-β", r'\bTGF-?[βbeta]\b|transforming growth factor-?[βbeta]\b|TGF-?B\b'),
    ("TGF-α", r'\bTGF-?[αalpha]\b|transforming growth factor-?[αalpha]\b|TGF-?A\b'),
    
    # Hepatocyte Growth Factor
    ("HGF", r'\bHGF\b|hepatocyte growth factor\b'),
    
    # Brain-Derived Neurotrophic Factor
    ("BDNF", r'\bBDNF\b|brain-?derived neurotrophic factor\b'),
    
    # Granulocyte Colony-Stimulating Factor
    ("G-CSF", r'\bG-?CSF\b|granulocyte colony-?stimulating factor\b'),
    
    # Granulocyte-Macrophage Colony-Stimulating Factor
    ("GM-CSF", r'\bGM-?CSF\b|granulocyte-?macrophage colony-?stimulating factor\b'),
    
    # Erythropoietin
    ("EPO", r'\bEPO\b|erythropoietin\b'),
    
    # Growth Hormone
    ("GH", r'\bGH\b|growth hormone\b|somatotropin\b'),
    
    # Interleukins (common growth-promoting ones)
    ("IL-2", r'\bIL-?2\b|interleukin-?2\b'),
    ("IL-3", r'\bIL-?3\b|interleukin-?3\b'),
    ("IL-6", r'\bIL-?6\b|interleukin-?6\b'),
    ("IL-7", r'\bIL-?7\b|interleukin-?7\b'),
    
    # Tumor Necrosis Factor
    ("TNF-α", r'\bTNF-?[αalpha]\b|tumor necrosis factor-?[αalpha]\b|TNF-?A\b'),
    
    # Connective Tissue Growth Factor
    ("CTGF", r'\bCTGF\b|connective tissue growth factor\b'),
    
    # Insulin-like Growth Factor Binding Proteins
    ("IGFBP", r'\bIGFBP-?\d+\b|insulin-?like growth factor binding protein-?\d*\b'),
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

