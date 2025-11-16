#!/usr/bin/env python3
"""
LLM-based parameter extraction from paper abstracts.
Extracts 5 parameters: Q1_Protein_Production, Q2_Reasoning, Q3_Recombinant_Proteins, Q4_Species, Q5_Host_Organism
"""

import os
import asyncio
import aiohttp
import pandas as pd
from typing import Optional


# Config for Predicta.bio LLM API
BASE_URL = os.getenv("BASE_URL", "https://ai.predicta.bio/v1").rstrip("/")
API_KEY = os.getenv("API_KEY", "anything")
MODEL_NAME = os.getenv("MODEL_NAME", "/home/mehmet-gunduz/models/Llama-3.2-3B-Instruct-Q6_K_L.gguf")


def build_prompt(title: str, abstract: str) -> str:
    """Build the prompt for LLM parameter extraction."""
    combined_text = f"{title} {abstract}".strip()
    return f"""You are a scientific research assistant. Analyze the following abstract and answer the questions below.

Abstract:
\"\"\"{combined_text}\"\"\"

Questions:
1. Is this paper related to protein production (expression and purification)? (yes or no)
(Classify as [YES] or [NO] using the following rule: YES if related to recombinant protein production, including expression, purification, optimization, cloning, structural studies, host engineering, metabolic engineering. NO if not related.)

2. Provide reasoning for your answer.

3. List any recombinant protein(s) expressed. (Do not use abbreviation, write full form)

4. List the specie(s) of the recombinant protein(s), if any. Categorise only in one of the following categories.[Human, Animal (non-human), Plants, Pathogens/Bacteria/Viruses, Others, NS (not specified)].

5. Identify the host organism(s), if mentioned. Categorise only in one of the following categories.[Escherichia coli (E. Coli), Bacteria (non-E. coli), Yeast, Mammalian, Insect, Plants, Other, NS (not specified)].

Please respond in a clear numbered list.
"""


async def query_local_llm(session: aiohttp.ClientSession, prompt: str) -> Optional[str]:
    """Query the Predicta.bio LLM API asynchronously."""
    try:
        async with session.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": "You are an expert biomedical research assistant. Answer questions based on the provided abstract. Do not guess."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.0
            },
            timeout=aiohttp.ClientTimeout(total=120)  # 2 minute timeout
        ) as response:
            response.raise_for_status()
            result = await response.json()
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[extract_parameters] LLM request failed: {e}")
        return None


def parse_llm_response(response: str) -> dict:
    """Parse LLM response and extract the 5 parameters."""
    result = {
        "Q1_Protein_Production": "",
        "Q2_Reasoning": "",
        "Q3_Recombinant_Proteins": "",
        "Q4_Species": "",
        "Q5_Host_Organism": ""
    }
    
    if not response:
        return result
    
    lines = response.strip().splitlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Try to match numbered items (1., 1), 1:, etc.)
        if line.startswith("1") and (line[1] in [".", ")", ":"] or len(line) > 1 and line[1:2].isspace()):
            content = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            result["Q1_Protein_Production"] = content
        elif line.startswith("2") and (line[1] in [".", ")", ":"] or len(line) > 1 and line[1:2].isspace()):
            content = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            result["Q2_Reasoning"] = content
        elif line.startswith("3") and (line[1] in [".", ")", ":"] or len(line) > 1 and line[1:2].isspace()):
            content = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            result["Q3_Recombinant_Proteins"] = content
        elif line.startswith("4") and (line[1] in [".", ")", ":"] or len(line) > 1 and line[1:2].isspace()):
            content = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            result["Q4_Species"] = content
        elif line.startswith("5") and (line[1] in [".", ")", ":"] or len(line) > 1 and line[1:2].isspace()):
            content = line.split(":", 1)[-1].strip() if ":" in line else line[2:].strip()
            result["Q5_Host_Organism"] = content
    
    return result


async def extract_parameters_from_dataframe(df: pd.DataFrame, start_idx: int = 0) -> pd.DataFrame:
    """
    Extract 5 parameters from each paper's title+abstract using LLM.
    
    Args:
        df: Input DataFrame with 'title' and 'abstract' columns
        start_idx: Starting row index (for resumability)
    
    Returns:
        DataFrame with 5 new columns added: Q1_Protein_Production, Q2_Reasoning, 
        Q3_Recombinant_Proteins, Q4_Species, Q5_Host_Organism
    """
    if df is None or df.empty:
        return df
    
    # Check for required columns
    if 'title' not in df.columns or 'abstract' not in df.columns:
        print(f"[extract_parameters] Warning: Missing 'title' or 'abstract' columns. Available columns: {list(df.columns)}")
        return df
    
    # Add output columns if they don't exist
    for col in ["Q1_Protein_Production", "Q2_Reasoning", "Q3_Recombinant_Proteins", "Q4_Species", "Q5_Host_Organism"]:
        if col not in df.columns:
            df[col] = ""
    
    # Determine start index (check for already processed rows)
    if start_idx == 0:
        # Find first unprocessed row
        unprocessed = df[df["Q1_Protein_Production"].isna() | (df["Q1_Protein_Production"] == "")]
        if not unprocessed.empty:
            start_idx = unprocessed.index[0]
        else:
            # All rows processed
            print(f"[extract_parameters] All rows already processed")
            return df
    
    print(f"[extract_parameters] Starting parameter extraction from row {start_idx} / {len(df)}")
    
    # Create aiohttp session for async requests
    async with aiohttp.ClientSession() as session:
        # Process each row
        for i in range(start_idx, len(df)):
            row = df.iloc[i]
            title = str(row.get("title", "")).strip()
            abstract = str(row.get("abstract", "")).strip()
            
            if not title and not abstract:
                print(f"[extract_parameters] Skipping row {i+1}: empty title and abstract")
                continue
            
            # Skip if already processed
            if pd.notna(row.get("Q1_Protein_Production")) and str(row.get("Q1_Protein_Production")).strip():
                continue
            
            prompt = build_prompt(title, abstract)
            print(f"[extract_parameters] Processing row {i+1}/{len(df)}...")
            
            response = await query_local_llm(session, prompt)
            
            if response:
                print(f"[extract_parameters] ✅ Response received for row {i+1}")
                # Parse response and update DataFrame
                parsed = parse_llm_response(response)
                for key, value in parsed.items():
                    df.at[i, key] = value
            else:
                print(f"[extract_parameters] ⚠️ No response for row {i+1}, leaving empty")
            
            # Small delay to avoid overwhelming the server
            await asyncio.sleep(0.5)
            
            # Print progress every 10 rows
            if (i + 1) % 10 == 0:
                processed = len(df[df["Q1_Protein_Production"].notna() & (df["Q1_Protein_Production"] != "")])
                print(f"[extract_parameters] Progress: {processed}/{len(df)} rows processed")
    
    print(f"[extract_parameters] ✅ Parameter extraction complete")
    return df

