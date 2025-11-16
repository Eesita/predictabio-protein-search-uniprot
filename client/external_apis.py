#!/usr/bin/env python3
"""
External API clients for fetching additional data.
"""

import os
import sys
from pathlib import Path
from typing import Optional, List
import pandas as pd


async def fetch_metadata(
    queries: List[str],
    sources: Optional[List[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    intelligent_query: bool = True,
    script_dir: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch metadata by running external Python script that returns a DataFrame.
    
    Args:
        queries: List of protein names or search terms
        sources: List of sources to search (default: ["pubmed", "semantic_scholar"])
        start_date: Start date in format "YYYY-MM" (default: "2020-01")
        end_date: End date in format "YYYY-MM" (default: "2020-03")
        intelligent_query: Whether to use intelligent query processing (default: False)
        script_dir: Path to article_fetcher directory (default: from ARTICLE_FETCHER_DIR env var)
    
    Returns:
        pandas DataFrame containing metadata results, or None if request fails
    """
    if sources is None:
        sources = ["pubmed", "semantic_scholar"]
    if start_date is None:
        start_date = "2020-01"
    if end_date is None:
        end_date = "2020-03"
    
    # Get script directory
    if script_dir is None:
        script_dir = os.getenv(
            "ARTICLE_FETCHER_DIR",
            "/Users/eesitasen/Desktop/predictabio/article_fetcher"
        )
    
    if not queries:
        print("[external_apis] No queries provided")
        return None
    
    print(f"[external_apis] Fetching metadata for queries: {queries}")
    print(f"[external_apis] Sources: {sources}, Date range: {start_date} to {end_date}")
    
    try:
        # Add article_fetcher directory to Python path (needed for article_fetcher module imports)
        article_fetcher_path = Path(script_dir).resolve()
        if str(article_fetcher_path) not in sys.path:
            sys.path.insert(0, str(article_fetcher_path))
        
        # Import the script module directly using importlib
        import importlib.util
        script_file = article_fetcher_path / "scripts" / "fetch_to_dataframe.py"
        
        if not script_file.exists():
            print(f"[external_apis] Script not found at: {script_file}")
            return None
        
        print(f"[external_apis] Loading script from: {script_file}")
        print(f"[external_apis] Article fetcher path: {article_fetcher_path}")
        print(f"[external_apis] Python path includes: {str(article_fetcher_path) in sys.path}")
        
        # Create a unique module name to avoid conflicts
        module_name = f"fetch_to_dataframe_{id(script_file)}"
        spec = importlib.util.spec_from_file_location(module_name, script_file)
        if spec is None or spec.loader is None:
            print(f"[external_apis] Failed to create module spec for: {script_file}")
            return None
        
        module = importlib.util.module_from_spec(spec)
        # Set __file__ to absolute path BEFORE adding to sys.modules so the script can find its parent directory
        # The script uses Path(__file__).parent.parent to add the article_fetcher directory to sys.path
        module.__file__ = str(script_file.resolve())
        # Also set __name__ to avoid conflicts
        module.__name__ = module_name
        # Add to sys.modules so imports within the module work correctly
        sys.modules[module_name] = module
        
        print(f"[external_apis] Module __file__ set to: {module.__file__}")
        print(f"[external_apis] Expected parent.parent: {Path(module.__file__).parent.parent}")
        
        # Execute the module (this will run the imports inside the script)
        try:
            spec.loader.exec_module(module)
        except ImportError as import_err:
            print(f"[external_apis] Import error while loading module: {import_err}")
            print(f"[external_apis] Error type: {type(import_err).__name__}")
            print(f"[external_apis] Module __file__: {getattr(module, '__file__', 'NOT SET')}")
            print(f"[external_apis] Script file path: {script_file}")
            print(f"[external_apis] Article fetcher path: {article_fetcher_path}")
            import traceback
            traceback.print_exc()
            # Clean up
            if module_name in sys.modules:
                del sys.modules[module_name]
            return None
        except Exception as module_err:
            print(f"[external_apis] Error executing module: {module_err}")
            print(f"[external_apis] Error type: {type(module_err).__name__}")
            import traceback
            traceback.print_exc()
            # Clean up
            if module_name in sys.modules:
                del sys.modules[module_name]
            return None
        
        # Get the async function from the module
        fetch_articles_to_dataframe = getattr(module, "fetch_articles_to_dataframe", None)
        if fetch_articles_to_dataframe is None:
            print(f"[external_apis] Function 'fetch_articles_to_dataframe' not found in module")
            print(f"[external_apis] Available attributes: {[attr for attr in dir(module) if not attr.startswith('_')]}")
            return None
        
        print(f"[external_apis] Successfully loaded fetch_articles_to_dataframe function")
        
        # Call the async function
        df = await fetch_articles_to_dataframe(
            queries=queries,
            sources=sources,
            start_date=start_date,
            end_date=end_date,
            intelligent_query=intelligent_query,
            print_progress=False  # Suppress progress messages in workflow
        )
        
        if df is not None and not df.empty:
            print(f"[external_apis] Successfully fetched {len(df)} articles")
            return df
        else:
            print("[external_apis] No articles found")
            return pd.DataFrame()  # Return empty DataFrame instead of None
            
    except ImportError as e:
        print(f"[external_apis] Import error: {e}")
        print(f"[external_apis] Error type: {type(e).__name__}")
        print(f"[external_apis] Make sure article_fetcher is accessible at: {script_dir}")
        import traceback
        traceback.print_exc()
        return None
    except Exception as e:
        print(f"[external_apis] Error fetching metadata: {e}")
        print(f"[external_apis] Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return None

