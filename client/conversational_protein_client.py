#!/usr/bin/env python3
"""
Protein Search - Dynamic LangGraph Workflow

Workflow name: protein_search_workflow
Goal: Obtain a single confirmed protein entry with full details from UniProt
Type: llm + tool + human-in-loop hybrid workflow
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set, Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
import pandas as pd

# Memory debugging
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("[WARNING] psutil not available - memory debugging will be limited")
from protein_search_client import ProteinSearchClient
from search_agent import SearchAgent
from external_apis import fetch_metadata
from scripts.filter1 import filter_growth_factors
from scripts.extract_parameters import extract_parameters_from_dataframe
from scripts.filter_human_ecoli import filter_human_ecoli_gf
from dotenv import load_dotenv
from database import get_db_connection, insert_metadata_bulk, update_pdf_found_from_verified_csv, update_methods_validation_from_csv

try:
    from langsmith import Client as LangSmithClient
    from langsmith.run_helpers import get_current_run_tree
    from langsmith import traceable
    from langchain_core.tracers import LangChainTracer
    from langchain_core.runnables import RunnableConfig
except ImportError:  # pragma: no cover - optional dependency
    LangSmithClient = None  # type: ignore
    get_current_run_tree = None  # type: ignore
    traceable = None  # type: ignore
    LangChainTracer = None  # type: ignore
    RunnableConfig = None  # type: ignore

load_dotenv()

# ============================================================================
# MEMORY DEBUGGING UTILITIES
# ============================================================================

def get_memory_usage() -> Dict[str, Any]:
    """Get current memory usage statistics"""
    if not PSUTIL_AVAILABLE:
        return {"error": "psutil not available"}
    try:
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        return {
            "rss_mb": mem_info.rss / 1024 / 1024,  # Resident Set Size in MB
            "vms_mb": mem_info.vms / 1024 / 1024,  # Virtual Memory Size in MB
            "percent": process.memory_percent(),
        }
    except Exception as e:
        return {"error": str(e)}

def log_memory_usage(context: str, cache_size: int = None):
    """Log memory usage with context"""
    mem = get_memory_usage()
    cache_info = f", Cache entries: {cache_size}" if cache_size is not None else f", Cache entries: {len(_metadata_cache)}"
    if "error" not in mem:
        print(f"[MEMORY DEBUG] {context}: RSS={mem.get('rss_mb', 0):.2f}MB, "
              f"VMS={mem.get('vms_mb', 0):.2f}MB, "
              f"Percent={mem.get('percent', 0):.2f}%{cache_info}")
    else:
        print(f"[MEMORY DEBUG] {context}: {mem.get('error', 'Unknown error')}{cache_info}")

def estimate_dataframe_size_mb(df: pd.DataFrame) -> float:
    """Estimate DataFrame memory usage in MB"""
    try:
        return df.memory_usage(deep=True).sum() / 1024 / 1024
    except:
        return 0.0

def estimate_cache_size_mb() -> float:
    """Estimate total cache size in MB"""
    total_size = 0
    for cache_id, metadata in _metadata_cache.items():
        records = metadata.get("records", [])
        # Rough estimate: each record ~1KB, plus overhead
        total_size += len(records) * 1024
    return total_size / 1024 / 1024

# ============================================================================
# METADATA CACHE
# ============================================================================

# In-memory cache for metadata (all datasets stored in cache)
# Key: cache_id (e.g., "metadata_thread123_1234567890")
# Value: Full metadata payload with records
_metadata_cache: Dict[str, Dict[str, Any]] = {}

# Mapping of thread_id to cache_ids for cleanup
_thread_cache_mapping: Dict[str, List[str]] = {}


def get_metadata_from_cache(cache_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full metadata from cache by cache_id"""
    return _metadata_cache.get(cache_id)


def cleanup_metadata_cache(thread_id: str):
    """Remove all cache entries for a given thread_id"""
    log_memory_usage(f"[CACHE CLEANUP START] Thread: {thread_id}", len(_metadata_cache))
    
    if thread_id in _thread_cache_mapping:
        cache_ids = _thread_cache_mapping[thread_id]
        for cache_id in cache_ids:
            if cache_id in _metadata_cache:
                del _metadata_cache[cache_id]
        del _thread_cache_mapping[thread_id]
        if cache_ids:
            log_memory_usage(f"[CACHE CLEANUP END] Thread: {thread_id}", len(_metadata_cache))
            print(f"[CACHE] Cleaned up {len(cache_ids)} cache entries for thread: {thread_id}")
    else:
        # Fallback: cleanup by pattern matching (for cache_ids created before mapping)
        # This handles cases where cache was created but mapping wasn't updated
        keys_to_remove = [
            key for key in _metadata_cache.keys()
            if key.startswith("metadata_")
        ]
        # Only remove if we have very few entries (safety check)
        if len(keys_to_remove) < 100:  # Safety threshold
            for key in keys_to_remove:
                del _metadata_cache[key]
            if keys_to_remove:
                log_memory_usage(f"[CACHE CLEANUP END] Pattern-based", len(_metadata_cache))
                print(f"[CACHE] Cleaned up {len(keys_to_remove)} cache entries (pattern-based cleanup)")


def register_cache_for_thread(thread_id: str, cache_id: str):
    """Register a cache_id for a thread_id to enable cleanup"""
    if thread_id not in _thread_cache_mapping:
        _thread_cache_mapping[thread_id] = []
    if cache_id not in _thread_cache_mapping[thread_id]:
        _thread_cache_mapping[thread_id].append(cache_id)


def cleanup_stale_cache(max_age_seconds: int = 3600):
    """Remove cache entries older than max_age_seconds (safety cleanup)"""
    current_time = time.time()
    stale_keys = []
    
    for cache_id, metadata in _metadata_cache.items():
        generated_at = metadata.get("generated_at", "")
        if generated_at:
            try:
                # Parse ISO timestamp
                gen_time = datetime.fromisoformat(generated_at.replace('Z', '+00:00'))
                if gen_time.tzinfo:
                    gen_timestamp = gen_time.timestamp()
                else:
                    gen_timestamp = datetime.timestamp(gen_time)
                
                if current_time - gen_timestamp > max_age_seconds:
                    stale_keys.append(cache_id)
            except (ValueError, AttributeError):
                # If timestamp parsing fails, skip
                pass
    
    for key in stale_keys:
        if key in _metadata_cache:
            del _metadata_cache[key]
    
    # Clean up thread mapping
    for thread_id, cache_ids in list(_thread_cache_mapping.items()):
        _thread_cache_mapping[thread_id] = [
            cid for cid in cache_ids if cid not in stale_keys
        ]
        if not _thread_cache_mapping[thread_id]:
            del _thread_cache_mapping[thread_id]
    
    if stale_keys:
        print(f"[CACHE] Cleaned up {len(stale_keys)} stale cache entries (older than {max_age_seconds}s)")

# ============================================================================
# LLM CLIENT
# ============================================================================

class LLMClient:
    def __init__(self):
        import requests
        self.requests = requests
        # LLM configuration from environment variables (required - must be in .env file)
        base_url = os.getenv("BASE_URL")
        if not base_url:
            raise ValueError("BASE_URL environment variable is required. Please set it in .env file.")
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        
        self.model = os.getenv("MODEL_NAME")
        if not self.model:
            raise ValueError("MODEL_NAME environment variable is required. Please set it in .env file.")
        
        self.api_key = os.getenv("API_KEY")
        if not self.api_key:
            raise ValueError("API_KEY environment variable is required. Please set it in .env file.")
        
        self._usage_events: List[Dict[str, Any]] = []
        self._request_count: int = 0

    def query(self, prompt: str) -> str:
        print(f"\n[LLM REQUEST]\n{prompt}\n")
        
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 500,
        }
        
        response = self.requests.post(
            self.endpoint,
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60
        )
        response_data = response.json()
        usage = response_data.get("usage")
        if usage:
            # normalize to avoid None entries
            self._usage_events.append({
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            })
        self._request_count += 1
        result = response_data["choices"][0]["message"]["content"].strip()
        print(f"[LLM RESPONSE]\n{result}\n")
        
        return result

    @property
    def usage_event_count(self) -> int:
        return len(self._usage_events)

    def get_usage_events_since(self, start_index: int) -> List[Dict[str, Any]]:
        if start_index < 0:
            start_index = 0
        return self._usage_events[start_index:]

    @property
    def total_requests(self) -> int:
        return self._request_count


# ============================================================================
# LANGSMITH + LLM JUDGE HELPERS
# ============================================================================

LANGSMITH_PROJECT = os.getenv("LANGSMITH_PROJECT", "protein-search-evals")
_LANGSMITH_CLIENT_SINGLETON: Optional['LangSmithClient'] = None

def get_langsmith_client() -> Optional['LangSmithClient']:
    """Lazy-load LangSmith client when credentials are available."""
    global _LANGSMITH_CLIENT_SINGLETON
    if LangSmithClient is None:
        print("[LangSmith] LangSmithClient import failed")
        return None
    if os.getenv("LANGSMITH_DISABLED", "").lower() in {"1", "true", "yes"}:
        print("[LangSmith] Client disabled via LANGSMITH_DISABLED")
        return None
    if _LANGSMITH_CLIENT_SINGLETON is not None:
        return _LANGSMITH_CLIENT_SINGLETON
    api_key_present = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key_present:
        print("[LangSmith] No API key found")
        return None
    try:
        print("[LangSmith] Initializing LangSmithClient...")
        _LANGSMITH_CLIENT_SINGLETON = LangSmithClient()
        print("[LangSmith] Client initialized successfully")
    except Exception as exc:  # pragma: no cover - network/init failure
        print(f"[LangSmith] Failed to initialize client: {exc}")
        import traceback
        traceback.print_exc()
        _LANGSMITH_CLIENT_SINGLETON = None
    return _LANGSMITH_CLIENT_SINGLETON

def _summarize_usage(events: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    """Aggregate token usage across LLM calls."""
    if not events:
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    prompt = 0
    completion = 0
    total = 0
    for entry in events:
        prompt += entry.get("prompt_tokens") or 0
        completion += entry.get("completion_tokens") or 0
        total += entry.get("total_tokens") or 0
    return {
        "prompt_tokens": prompt or None,
        "completion_tokens": completion or None,
        "total_tokens": total or None,
    }

async def _invoke_gemini_judge(prompt: str) -> Optional[str]:
    """Call Gemini with the supplied prompt, returning raw text output."""
    try:
        from google import genai
    except ImportError:
        print("[LLM Judge] google-genai not installed. Skipping Gemini evaluation.")
        return None
    
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[LLM Judge] Skipping Gemini evaluation (missing API key).")
        return None

    model = os.getenv("GEMINI_JUDGE_MODEL", "gemini-2.0-flash-exp")

    def _call() -> Optional[str]:
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text if response else None
        except Exception as exc:  # pragma: no cover - network issues
            print(f"[LLM Judge] Gemini request failed: {exc}")
            return None

    return await asyncio.to_thread(_call)

def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Attempt to parse a JSON object from arbitrary text output."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if not candidate:
        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        candidate = brace_match.group(0) if brace_match else None
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None

async def run_llm_judge(
    user_message: str,
    assistant_message: str,
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Evaluate a response with Gemini-based LLM judge.
    
    This function is wrapped with @traceable to automatically create feedback
    on the parent workflow run in LangSmith.

    Returns:
        dict with keys {"score": float, "rationale": str} or None on failure.
    """
    decision = context.get("decision", "unknown")
    
    # Path-specific evaluation criteria
    if decision == "details":
        criteria = """
        The system successfully found and returned protein details. Evaluate:
        - Did it correctly extract protein name and organism from user query? (entity_extraction node)
        - Did the search return relevant results? (dynamic_search node)
        - Were there appropriate retry attempts if needed? (retry_node)
        - Did narrow_down_node or search_clarification properly handle results?
        - Are the returned protein details accurate and complete? (protein_details_node)
        - Does the assistant provide useful information (accession, name, organism, length, etc.)?
        - Is the response clear and helpful?
        
        Node-level analysis if score < 7:
        * entity_extraction: Did it correctly parse the user's query?
        * dynamic_search: Were the search queries properly constructed?
        * retry_node: Were retry strategies appropriate and effective?
        * narrow_down_node: Did it help reduce results intelligently?
        * search_clarification: Were clarification questions helpful?
        * protein_details_node: Were protein details retrieved correctly?
        """
    elif decision == "await_user":
        criteria = """
        The system is requesting clarification from the user. Evaluate:
        - Was the clarification request necessary (e.g., missing protein name or organism)?
        - Is the request clear and specific about what's needed?
        - Does the request help the user provide better information?
        - Is the tone appropriate and helpful?
        
        Node-level analysis if score < 7:
        * entity_extraction: Should it have extracted more from the initial query?
        * search_clarification: Was the question clear and actionable?
        * dynamic_search: Should it have searched with partial information?
        """
    elif decision == "fallback":
        criteria = """
        The system hit a fallback/error state. Evaluate:
        - Was the error message helpful?
        - Did the system gracefully handle the failure?
        - Does it guide the user to next steps?
        
        Node-level analysis if score < 7:
        * entity_extraction: Did it fail to parse the query correctly?
        * dynamic_search: Did searches fail repeatedly?
        * retry_node: Did retry logic exhaust all options?
        * Overall workflow: Was there a complete system failure?
        """
    else:
        criteria = """
        Evaluate general quality: factual accuracy, completeness, relevance, and helpfulness.
        Provide detailed node-by-node analysis if issues are detected.
        """
    
    evaluation_prompt = f"""
You are an expert biology assistant evaluating a protein search conversation from UniProt database.
Provide an impartial score from 1 (poor) to 10 (excellent) and a detailed analysis.

Decision Path: {decision}

Evaluation Criteria:
{criteria}

Context:
- User asked: {user_message}
- Assistant replied: {assistant_message}
- Additional context: {json.dumps(context, indent=2)}

Instructions:
- Base judgement on factual accuracy, completeness, relevance, and user experience
- Consider the appropriate workflow path and whether it was executed correctly
- **DEEP DIVE ANALYSIS**: If the score is below 7, provide a detailed failure analysis:
  * Identify which specific workflow nodes failed or underperformed
  * Explain what went wrong at each problematic node
  * Suggest specific interventions or improvements needed
  * Consider: entity_extraction, dynamic_search, retry_node, narrow_down_node, search_clarification, protein_details_node
- For high scores (8-10), highlight what worked well
- Respond with ONLY valid JSON: {{"score": <number 1-10>, "rationale": "<detailed analysis including node-level diagnosis if score < 7>"}}
""".strip()

    raw_output = await _invoke_gemini_judge(evaluation_prompt)
    if not raw_output:
        return None

    parsed = _extract_json_object(raw_output)
    if not parsed:
        print("[LLM Judge] Unable to parse Gemini output as JSON.")
        return None

    score = parsed.get("score")
    rationale = parsed.get("rationale") or parsed.get("reason") or ""
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        print("[LLM Judge] Gemini score missing or invalid.")
        return None

    score_value = max(1.0, min(10.0, score_value))
    judge_output = {"score": score_value, "rationale": rationale.strip()}
    print(f"[LLM Judge] Score: {score_value}/10 | Rationale: {judge_output['rationale']}")
    return judge_output

def _safe_create_feedback(
    client: "LangSmithClient",
    run_id: Optional[str],
    key: str,
    *,
    score: Optional[float] = None,
    value: Optional[Any] = None,
    comment: Optional[str] = None,
    source: Optional[str] = None,
) -> None:
    """Attach feedback to a LangSmith run, swallowing individual failures."""
    if not run_id:
        print(f"[LangSmith] Skipping feedback '{key}': no run_id")
        return
    try:
        client.create_feedback(
            run_id=run_id,
            key=key,
            score=score,
            value=value,
            comment=comment,
            source=source,
        )
        print(f"[LangSmith] Feedback '{key}' attached successfully")
    except Exception as exc:  # pragma: no cover - network issues
        print(f"[LangSmith] Failed to create feedback '{key}': {exc}")

# ============================================================================
# STATE / MEMORY
# ============================================================================

class WorkflowState(TypedDict):
    # conversation - AUTO-APPEND enabled!
    chat_history: Annotated[List[Dict[str, str]], add_messages]
    # user input
    query: Optional[str]
    # extracted entities
    protein_name: Optional[str]
    organism: Optional[str]
    protein_accession: Optional[str]
    # search and results
    protein_results: List[Any]
    selected_protein: Optional[Any]
    # messaging flags
    clarification_message: Optional[str]
    assistant_message: Optional[str]
    # 🔄 CHANGED: refinement_terms structure
    refinement_terms: Dict[str, Any]
    # dynamic query extras
    optimized_query: Optional[str]
    search_context: Optional[str]
    applied_filters: Dict[str, Any]
    search_facets: Dict[str, Any]
    search_attempts: List[Dict[str, Any]]
    filter_summary_text: Optional[str]

    # 🆕 NEW
    retry_count: int
    retry_alternates: List[str]  # Store list of alternates to try
    retry_alternate_index: int   # Track which alternate we're currently trying
    
    # Metadata payload from external API (serialized for checkpointer)
    protein_metadata: Optional[Dict[str, Any]]
    
    # Path to final filtered CSV file for PDF downloader
    final_filtered_csv_path: Optional[str]
    
    # Path to verified PDFs folder from PDF downloader API
    pdf_verified_folder_path: Optional[str]
    
    # PDF processing results from GROBID extraction
    pdf_processing_results: Optional[Dict[str, Any]]
    
    # Methods validation results from classification
    methods_validation_results: Optional[Dict[str, Any]]

def _state_context_snapshot(state: WorkflowState) -> Dict[str, Any]:
    """Return a trimmed snapshot of the current state for logging/evaluation."""
    selected = state.get("selected_protein")
    selected_summary = None
    if selected:
        selected_summary = {
            "accession": getattr(selected, "accession", None),
            "name": getattr(selected, "name", None),
            "organism": getattr(selected, "organism", None),
            "length": getattr(selected, "length", None),
            "mass": getattr(selected, "mass", None),
        }

    attempt_summary: List[Dict[str, Any]] = []
    search_attempts = state.get("search_attempts", [])
    for att in search_attempts[-5:]:
        steps = att.get("steps") or []
        attempt_summary.append(
            {
                "query": att.get("query"),
                "result_count": att.get("result_count"),
                "tool_calls": len(steps),
            }
        )

    return {
        "protein_name": state.get("protein_name"),
        "organism": state.get("organism"),
        "retry_count": state.get("retry_count", 0),
        "result_count": len(state.get("protein_results", [])),
        "selected_protein": selected_summary,
        "applied_filters": state.get("applied_filters", {}),
        "filter_summary_text": state.get("filter_summary_text"),
        "search_attempts": attempt_summary,
    }

async def log_langsmith_evaluation(
    *,
    user_message: str,
    assistant_message: str,
    state: WorkflowState,
    decision: str,
    start_time: datetime,
    end_time: datetime,
    latency_s: float,
    llm_usage_events: List[Dict[str, Any]],
    llm_request_count: int,
    judge_result: Optional[Dict[str, Any]],
) -> None:
    """Send LangSmith run and feedback metrics."""
    client = get_langsmith_client()
    if not client:
        print("[LangSmith] Skipping evaluation logging: no client configured")
        return
    
    # Try to get the current run from auto-tracing
    run_id = None
    if get_current_run_tree is not None:
        try:
            print(f"[LangSmith] Attempting to get current run tree...")
            current_run = get_current_run_tree()
            print(f"[LangSmith] get_current_run_tree returned: {current_run}")
            if current_run:
                run_id = str(current_run.id)
                print(f"[LangSmith] Found auto-traced run: {run_id}")
            else:
                print(f"[LangSmith] get_current_run_tree returned None")
        except Exception as exc:
            print(f"[LangSmith] Could not get current run tree: {exc}")
            import traceback
            traceback.print_exc()
    
    if not run_id:
        print("[LangSmith] Warning: No run_id available for feedback attachment")
        return
    
    print(f"[LangSmith] Attaching feedback to run_id: {run_id}")
    print(f"[LangSmith] Starting evaluation logging for decision: {decision}")

    usage_summary = _summarize_usage(llm_usage_events)
    search_attempts = state.get("search_attempts", [])
    tool_calls = sum(len((att.get("steps") or [])) for att in search_attempts)
    state_snapshot = _state_context_snapshot(state)

    # Latency feedback
    _safe_create_feedback(
        client,
        run_id,
        "latency_s",
        value=latency_s,
        comment=f"Decision: {decision}",
    )

    # Retry/tool metrics
    _safe_create_feedback(
        client,
        run_id,
        "retries",
        value=state.get("retry_count", 0),
    )
    _safe_create_feedback(
        client,
        run_id,
        "tool_calls",
        value=tool_calls,
    )

    # Cost metrics if available
    if any(usage_summary.values()):
        _safe_create_feedback(
            client,
            run_id,
            "token_usage",
            value=usage_summary,
        )

    # Quality score from judge
    if judge_result:
        _safe_create_feedback(
            client,
            run_id,
            "quality_score",
            score=judge_result.get("score"),
            comment=judge_result.get("rationale"),
            source="gemini-judge",
        )

    # Store context snapshot for debugging
    _safe_create_feedback(
        client,
        run_id,
        "state_snapshot",
        value=state_snapshot,
    )

# ============================================================================
# LANGGRAPH WORKFLOW: Protein Search and Selection Flow
# ============================================================================

def build_workflow(llm_client: 'LLMClient', mcp: 'ProteinSearchClient'):
    """Create LangGraph workflow covering extraction, search, refinement, selection, and details."""

    search_agent = SearchAgent(mcp)

    def entity_extraction_node(state: WorkflowState) -> Dict[str, Any]:
        """Extract entities from current user message only."""
        print("\n[entity_extraction] Extracting entities from user input...")
        
        user_message = state.get("query") or ""
        
        prompt = f"""You are extracting protein search context from conversation.

    USER MESSAGE: "{user_message}"

    CURRENT STATE:
    - protein_name: {state.get("protein_name")}
    - organism: {state.get("organism")}
    - refinements: {json.dumps(state.get("refinement_terms", {}), indent=2)}
    - chat_history: {list(state.get("chat_history", []))}

    CRITICAL INSTRUCTIONS:
    - If protein_name & organism are already set,
        - The user is mostly providing refinement terms, so extract the refinement terms.
        - Map abbreviations or short terms to the most relevant refinement entity (e.g., gene_symbols).
    - Use the last message in chat_history to understand if the user is replying to previous assistant message or not. for example, last assistant message may ask the user to provide specific gene symbol or other refinement terms, and the user input is that. you have to correctly identify it.

    Extract any of these entities if present from the user's message:
    - accession: UniProt accession number (e.g., "P01308", "Q99999")
    - protein_name: Protein/gene name (e.g., "EGFR", "p53", "Angiopoietin-1")
    - organism: Species (e.g., "human", "mouse", "Homo sapiens")
    - gene_symbols: Specific gene symbols (e.g., ["LIF", "ANGPT1"])
    - functional_role: receptor, enzyme, kinase, ligand, inhibitor, etc.
    - localizations: nucleus, membrane, cytoplasm, mitochondria, etc.
    - go_terms: GO identifiers (e.g., ["GO:0005737"])
    - keywords: Descriptive terms
    - ptms: phosphorylation, glycosylation, acetylation, ubiquitination, methylation
    - domains: Protein domains (e.g., ["SH2", "kinase domain"])
    - pathways: Biological pathways (e.g., ["MAPK pathway"])
    - disease_association: Disease names (e.g., "cancer", "Alzheimer's")
    - length: Amino acid length as {{"min": <int>, "max": <int>}}
    - "over 500 aa" → {{"min": 500, "max": null}}
    - "less than 300 aa" → {{"min": null, "max": 300}}
    - "200-500 aa" → {{"min": 200, "max": 500}}
    - mass: Molecular mass in Daltons as {{"min": <int>, "max": <int>}}
    - "100-200 kDa" → {{"min": 100000, "max": 200000}}

    Return JSON with ONLY the fields found in the message. Omit null/empty fields.

    {{
    "protein_name": "string or null",
    "organism": "string or null",
    "refinements": {{
        "gene_symbols": ["..."],
        "functional_role": "string",
        "localizations": ["..."],
        "go_terms": ["..."],
        "keywords": ["..."],
        "ptms": ["..."],
        "domains": ["..."],
        "pathways": ["..."],
        "disease_association": "string",
        "length": {{"min": int, "max": int}},
        "mass": {{"min": int, "max": int}}
        }}
    }}

    Examples:
    Input: "EGFR in nucleus"
    Output: {{"protein_name": "EGFR", "refinements": {{"localizations": ["nucleus"]}}}}

    Input: "phosphorylated"
    Output: {{"refinements": {{"ptms": ["phosphorylation"]}}}}

    Input: "LIF"
    Output: {{"protein_name": "LIF"}}

    Input: "less than 500 amino acids"
    Output: {{"refinements": {{"length": {{"min": 0, "max": 500}}}}}}

    Input: "58aa"
    Output: {{"refinements": {{"length": {{"min": 58, "max": 58}}}}}}

    Input: "human p53"
    Output: {{"protein_name": "p53", "organism": "human"}}
    """
        
        try:
            response = llm_client.query(prompt)
        except Exception as e:
            print(f"[entity_extraction] LLM error: {e}")
            raise
        
        # Extract JSON from response
        json_str = None
        
        # Try markdown code fence
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        
        # Try raw JSON object
        if not json_str:
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
        
        # Parse extracted entities
        protein = None
        org = None
        accession = None
        refinements = {}
        
        if json_str:
            try:
                # Fix Python None -> JSON null before parsing
                json_str = re.sub(r'\bNone\b', 'null', json_str)
                extracted = json.loads(json_str)
                
                # Extract core entities
                if extracted.get("protein_name"):
                    protein = extracted["protein_name"].strip()
                
                if extracted.get("organism"):
                    org = _normalize_organism_name(extracted["organism"].strip())

                if extracted.get("accession"):
                    accession = extracted["accession"].strip()
                
                # Extract refinements - check both nested and top-level
                extracted_refs = extracted.get("refinements", {})
                
                # 🔧 FIX: Handle refinements that might be at top level (LLM sometimes returns them there)
                # List of all possible refinement fields
                refinement_fields = {
                    # List fields
                    "gene_symbols": list,
                    "localizations": list,
                    "go_terms": list,
                    "keywords": list,
                    "ptms": list,
                    "domains": list,
                    "pathways": list,
                    # String fields
                    "functional_role": str,
                    "disease_association": str,
                    # Dict fields (special handling)
                    "length": dict,
                    "mass": dict,
                }
                
                # First, collect from nested refinements
                for field, expected_type in refinement_fields.items():
                    if extracted_refs.get(field):
                        value = extracted_refs[field]
                        # Validate type based on expected type
                        is_valid = False
                        if expected_type == list:
                            is_valid = isinstance(value, list) and len(value) > 0
                        elif expected_type == dict:
                            # For dict fields (length, mass), accept any dict (may have null values)
                            is_valid = isinstance(value, dict) and len(value) > 0
                        elif expected_type == str:
                            is_valid = isinstance(value, str) and value.strip()
                        
                        if is_valid:
                            refinements[field] = value
                
                # Then, check top level for any refinement fields (LLM might put them there)
                for field, expected_type in refinement_fields.items():
                    if field in extracted and field not in refinements:
                        value = extracted[field]
                        # Validate type based on expected type
                        is_valid = False
                        if expected_type == list:
                            is_valid = isinstance(value, list) and len(value) > 0
                        elif expected_type == dict:
                            # For dict fields (length, mass), accept any dict (may have null values)
                            is_valid = isinstance(value, dict) and len(value) > 0
                        elif expected_type == str:
                            is_valid = isinstance(value, str) and value.strip()
                        
                        if is_valid:
                            refinements[field] = value
                            print(f"[entity_extraction] Found {field} at top level, moved to refinements")
                        
            except json.JSONDecodeError as e:
                print(f"[entity_extraction] JSON parse error: {e}")
                print(f"[entity_extraction] Attempted: {json_str[:200]}")
        
        # Fallback: regex detect accession if not provided
        if accession is None:
            accession_match = re.search(r"\b(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]{5})\b", user_message, re.IGNORECASE)
            if accession_match:
                accession = accession_match.group(0)
        
        # Build summary
        summary_parts = []
        if protein:
            summary_parts.append(f"protein={protein}")
        if org:
            summary_parts.append(f"organism={org}")
        if accession:
            summary_parts.append(f"accession={accession}")
        if refinements:
            summary_parts.append(f"refinements={list(refinements.keys())}")
        
        summary = "Extracted → " + ", ".join(summary_parts) if summary_parts else "No entities found"
        print(f"[entity_extraction] {summary}")
        
        # 🔧 FIX: Only return fields that were actually extracted (not None)
        # This prevents overwriting existing state values with None
        update_dict = {
            "chat_history": [{"role": "assistant", "content": summary}],  # Auto-append!
        }
        
        # Only include protein_name if it was extracted (not None)
        if protein is not None:
            update_dict["protein_name"] = protein
        
        # Only include organism if it was extracted (not None)
        if org is not None:
            update_dict["organism"] = org

        if accession is not None:
            update_dict["protein_accession"] = accession
        
        # Merge refinements: preserve existing refinements and only update new ones
        existing_refinements = state.get("refinement_terms", {})
        if refinements:
            merged_refinements = dict(existing_refinements)
            merged_refinements.update(refinements)
            update_dict["refinement_terms"] = merged_refinements
        
        return update_dict

    # entity_clarification (Human) - uses interrupt!
    def entity_clarification_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[entity_clarification] Requesting missing information from user...")
        missing: List[str] = []
        if not state.get("protein_name"):
            if not state.get("protein_accession"):
                missing.append("protein name (gene symbol, protein name, or UniProt accession)")
        if not state.get("organism"):
            missing.append("organism (e.g., Homo sapiens, Mus musculus)")
        message = f"Please provide: {', '.join(missing)}"
        
        # Set the message in state so it's available in the result
        updates = {
            "assistant_message": message,
            "clarification_message": message,
            "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
        }
        
        # Pause with interrupt and return user response
        user_response = interrupt(message)
        
        # Update query field with user's response - let entity_extraction handle the extraction
        if user_response:
            updates["query"] = user_response.strip()
        
        # Return updates - will route to entity_extraction via edge
        return updates

    # dynamic_search (Tool + LLM query builder)
    async def dynamic_search_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[dynamic_search] Building query from structured refinements...")
        
        accession = state.get("protein_accession")
        protein = state.get("protein_name") or ""
        organism = state.get("organism")
        refinement_terms = state.get("refinement_terms", {})
        
        if accession:
            search_query = accession
            filters_text = f"accession: {accession}"
        else:
            # Build query using simple helper (MCP server will resolve organism taxonomy)
            search_query, filters_text = _build_query_from_refinements(
                protein_name=protein,
                organism=organism,
                refinement_terms=refinement_terms
            )
        
        print(f"[dynamic_search] Query: {search_query}")
        print(f"[dynamic_search] Filters: {filters_text}")
        
        # Execute search
        outcome = await search_agent.search(search_query, size=100)
        
        for step in outcome.steps:
            print(f"[search_agent] tool={step.tool} hits={step.hits} query='{step.query}'")
        
        results = outcome.results
        effective_query = outcome.normalized_query or search_query
        count = len(results)
        facets = _summarize_facets(results)
        
        # Record attempt
        filters_used = {"protein": protein, "organism": organism, "refinements": refinement_terms}
        if accession:
            filters_used["accession"] = accession
        attempt_record = {
            "query": effective_query,
            "result_count": count,
            "filters": filters_used,
            "steps": [step.__dict__ for step in outcome.steps],
        }
        attempts = _update_attempt_history(state.get("search_attempts", []), attempt_record)
        
        # Summary for chat
        summary_parts = [f"Search → {count} hits", f"query: {effective_query}"]
        if filters_text != "No refinements":
            summary_parts.append(f"filters: {filters_text}")
        summary = " | ".join(summary_parts)
        
        applied_filters = {"protein": protein, "organism": organism, **refinement_terms}
        if accession:
            applied_filters["accession"] = accession
        return {
            "protein_results": results,
            "optimized_query": effective_query,
            "applied_filters": applied_filters,
            "search_facets": facets,
            "search_attempts": attempts,
            "filter_summary_text": filters_text,
            "chat_history": [{"role": "assistant", "content": summary}],  # Auto-append!
        }

    # retry_node (LLM + Tool) - Smart retry using optimized queries
    RETRY_PROMPT = """You are assisting in resolving a failed UniProt search by suggesting biologically meaningful alternate protein names.

    CURRENT STATE:
    - protein_name: {protein_name}
    - organism: {organism}
    - search_query: {search_query}
    - result_count: 0

    SEARCH ATTEMPT HISTORY:
    {attempt_history}

    TASK:
    - Suggest 1 alternate protein name or gene symbol that could match the intended protein. 
    - Base your reasoning on biological context and common naming patterns, not just text formatting.
    - If the given name looks like a fusion protein, try to split it into likely components and suggest the most likely standard UniProt name.
    - Normalize casing and remove artifacts (underscores, tags, if they appear to be formatting artifacts.
    
    Return ONLY JSON:
    {{
    "alternates": ["name1"]
    }}
    """

    async def retry_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[retry_node] Zero results → checking alternate protein names...")
        
        protein_name = state.get("protein_name") or ""
        organism = state.get("organism")
        search_query = state.get("optimized_query") or ""
        attempts = state.get("search_attempts", [])
        
        # 🔧 FIX: Check if we already have alternates to try
        existing_alternates = state.get("retry_alternates", [])
        alternate_index = state.get("retry_alternate_index", 0)
        
        # Check if we've exhausted the single retry alternate
        # Flow: Generate alternate (index=0) -> dynamic_search -> route back to retry_node
        # At this point, if alternate_index >= len(alternates), we've tried all alternates
        print(f"[retry_node] Exhaustion check: alternate_index={alternate_index}, len(existing_alternates)={len(existing_alternates) if existing_alternates else 0}")
        
        # If we've tried all alternates (alternate_index >= len(alternates))
        if existing_alternates and alternate_index >= len(existing_alternates):
            # We've tried the alternate, check if we got any results
            current_results = state.get("protein_results", [])
            print(f"[retry_node] Exhaustion condition met: results={len(current_results)}")
            if len(current_results) == 0:
                print(f"[retry_node] Retry alternate exhausted, no results found")
                message = "Tried an alternate protein name but couldn't find results. Please provide a different protein name or more details."
                print(f"[retry_node] Returning exhaustion message: {message}")
                return {
                    "assistant_message": message,
                    "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
                }
        
        # If we have an alternate and haven't tried it yet (alternate_index < len(alternates))
        # This happens after dynamic_search fails and routes back to retry_node
        # We need to increment the index to indicate we've tried it, then check exhaustion
        if existing_alternates and alternate_index < len(existing_alternates):
            # We just tried the alternate (search ran and failed), increment index
            print(f"[retry_node] Alternate at index {alternate_index} was tried, incrementing to {alternate_index + 1}")
            new_index = alternate_index + 1
            # Now check if we've exhausted (after incrementing)
            if new_index >= len(existing_alternates):
                # We've exhausted all alternates
                current_results = state.get("protein_results", [])
                if len(current_results) == 0:
                    print(f"[retry_node] Retry alternate exhausted after increment, no results found")
                    message = "Tried an alternate protein name but couldn't find results. Please provide a different protein name or more details."
                    return {
                        "assistant_message": message,
                        "retry_alternate_index": new_index,  # Update index
                        "chat_history": [{"role": "assistant", "content": message}],
                    }
            # If not exhausted yet (shouldn't happen with 1 alternate, but keep for safety)
            return {
                "retry_alternate_index": new_index,
            }
        
        # Generate new alternates only if we don't have any yet
        print("[retry_node] Generating alternate protein names...")
        
        # Format attempt history
        attempt_history = "\n".join([
            f"Attempt {i+1}: query='{a.get('query')}', results={a.get('result_count')}"
            for i, a in enumerate(attempts[-3:])
        ])
        
        # Get LLM suggestions
        prompt = RETRY_PROMPT.format(
            protein_name=protein_name,
            organism=organism or "not specified",
            search_query=search_query,
            attempt_history=attempt_history or "No previous attempts"
        )
        
        response = llm_client.query(prompt)
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        
        alternate = None
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                alternates_list = parsed.get("alternates", [])
                # Take only the first alternate (we only want 1 retry candidate)
                if alternates_list and len(alternates_list) > 0:
                    alternate = alternates_list[0]
            except json.JSONDecodeError:
                pass
        
        # Filter out if it's the same as current protein_name
        if alternate and alternate == protein_name:
            alternate = None
        
        # If no valid alternate found, end workflow
        if not alternate:
            message = "Could not find an alternate protein name. Please provide a different protein name or more details."
            print(f"[retry_node] No valid alternate found, ending workflow")
            return {
                "assistant_message": message,
                "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
            }
        
        print(f"[retry_node] Generated 1 alternate: {alternate}")
        
        # Store alternate in state and try it
        # Set alternate_index to 0 initially (we're about to try index 0)
        # After dynamic_search runs and fails, route_from_retry will route back to retry_node
        # At that point, retry_node will increment the index and check exhaustion
        message = f"Trying alternate name: '{alternate}'"
        
        return {
            "protein_name": alternate,
            "retry_alternates": [alternate],  # Store as list for compatibility with exhaustion check
            "retry_alternate_index": 0,  # Start at 0 - we're about to try it
            "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
        }

    # narrow_down_node (LLM)
    def narrow_down_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[narrow_down_node] Too many results → generating refinement suggestions...")
        
        results = state.get("protein_results", [])
        attempts = state.get("search_attempts", [])
        refinement_terms = state.get("refinement_terms", {})  # 🔄 CHANGED
        facets = state.get("search_facets", {})
        filter_text = state.get("filter_summary_text") or "No refinements"
        
        # Check if all refinement fields are populated
        populated_count = sum([
            bool(refinement_terms.get("gene_symbols")),
            bool(refinement_terms.get("functional_role")),
            bool(refinement_terms.get("localizations")),
            bool(refinement_terms.get("go_terms")),
            bool(refinement_terms.get("keywords")),
            bool(refinement_terms.get("ptms")),
            bool(refinement_terms.get("domains")),
            bool(refinement_terms.get("pathways")),
            bool(refinement_terms.get("disease_association")),
            bool(refinement_terms.get("length", {}).get("min") or refinement_terms.get("length", {}).get("max")),
            bool(refinement_terms.get("mass", {}).get("min") or refinement_terms.get("mass", {}).get("max")),
        ])
        
        # If 8+ fields populated, show list directly
        if populated_count >= 8 and len(attempts) >= 2:
            print("[narrow_down_node] Most refinement fields populated - showing list")
            if len(results) <= 10:
                message = _format_result_list(results) + "\n\nPlease select one (reply with number or accession)."
            else:
                message = (
                    f"Found {len(results)} entries with current filters. Showing first 10:\n\n"
                    f"{_format_result_list(results[:10])}\n\n"
                    f"Please select one (reply with number or accession), or provide more specific criteria."
                )
            return {
                "clarification_message": message,
                "assistant_message": message,
                "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
            }
        
        # Otherwise use LLM to suggest refinements
        normalized_query = state.get("optimized_query") or ""
        prompt = _build_narrow_prompt(
            normalized_query=normalized_query,
            result_count=len(results),
            filters_text=filter_text,
            facets=facets,
            attempts=attempts,
        )
        
        message = llm_client.query(prompt)
        
        return {
            "clarification_message": message,
            "assistant_message": message,
            "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
        }

    # search_clarification (Human) - uses interrupt!
    def search_clarification_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[search_clarification] Asking user to refine search...")
        base = state.get("clarification_message") or (
            "Please refine your query: specify gene symbol, isoform, domain, or exact organism."
        )
        
        # Set message in state
        message = base
        updates = {
            "assistant_message": message,
            "clarification_message": message,
            "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
        }
        
        # Pause with interrupt and wait for user input
        user_response = interrupt(message)
        
        # Parse user response as refinement
        if user_response:
            # Update query so entity_extraction can process it on the next loop
            updates["query"] = user_response.strip()
        
        # Return updates - will loop back to entity_extraction via edge
        return updates

    # select_node (Human) - uses interrupt!
    def select_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[select_node] Presenting options for user selection (1–10 results)...")
        results = state.get("protein_results", [])
        lines: List[str] = ["Please select one protein (reply with number or accession):\n"]
        for i, r in enumerate(results[:10], 1):
            line = f"{i}. {getattr(r, 'accession', '')} - {getattr(r, 'name', '')} ({getattr(r, 'organism', '')})"
            if getattr(r, "length", None):
                line += f" | length: {getattr(r, 'length')} aa"
            lines.append(line)
        message = "\n".join(lines)
        
        # Pause with interrupt and get user selection
        user_selection = interrupt(message)
        
        # Parse selection by number
        updates = {}
        if user_selection and user_selection.isdigit():
            idx = int(user_selection) - 1
            if 0 <= idx < len(results):
                updates["selected_protein"] = results[idx]
        else:
            # Try accession code
            accession_match = re.search(r"\b[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9]{5}\b", user_selection or "")
            if accession_match:
                acc = accession_match.group(0)
                for r in results:
                    if getattr(r, "accession", "") == acc:
                        updates["selected_protein"] = r
                        break
        
        return updates

    # protein_details_node (Tool)
    async def protein_details_node(state: WorkflowState) -> Dict[str, Any]:
        print("\n[protein_details_node] Fetching details for selected protein...")
        selected = state.get("selected_protein")
        if not selected:
            return {}
        
        # Enrich protein details by re-querying UniProt
        accession = getattr(selected, "accession", None) or ""
        details = selected
        if accession:
            enriched = await mcp.search_proteins(query=accession, organism=None, size=1)
            if enriched:
                details = enriched[0]
        
        # Format protein details for display
        fields = [
            f"Accession: {getattr(details, 'accession', '')}",
            f"Name: {getattr(details, 'name', '')}",
            f"Organism: {getattr(details, 'organism', '')}",
        ]
        if getattr(details, "length", None):
            fields.append(f"Length: {details.length} aa")
        if getattr(details, "mass", None):
            fields.append(f"Mass: {details.mass} Da")
        if getattr(details, "gene_names", None):
            fields.append(f"Genes: {', '.join(details.gene_names)}")
        summary = "\n".join(fields)
        message = f"Confirmed protein details:\n{summary}"
        return {
            "selected_protein": details,
            "assistant_message": message,
            "chat_history": [{"role": "assistant", "content": message}],  # Auto-append!
        }

    # metadata_fetcher_node (API call)
    async def metadata_fetcher_node(state: WorkflowState) -> Dict[str, Any]:
        """Fetch metadata for the selected protein from external sources."""
        print("\n[metadata_fetcher_node] Fetching metadata for selected protein...")

        selected = state.get("selected_protein")
        if not selected:
            print("[metadata_fetcher_node] No selected protein, skipping metadata fetch")
            return {}

        # Extract accession from selected protein
        accession = getattr(selected, "accession", None) if selected else None

        protein_name = getattr(selected, "name", None) or state.get("protein_name", "")
        if not protein_name:
            print("[metadata_fetcher_node] No protein name available, skipping metadata fetch")
            return {}

        print(f"[metadata_fetcher_node] Fetching metadata for: {protein_name}")

        metadata_sources = ["pubmed", "semantic_scholar"]
        metadata_start = "2000-01"
        metadata_end = "2024-12"
        metadata_df = await fetch_metadata(
            queries=[protein_name],
            sources=metadata_sources,
            start_date=metadata_start,
            end_date=metadata_end,
            intelligent_query=True
        )

        if metadata_df is None:
            print("[metadata_fetcher_node] Metadata fetch failed or returned None")
            return {"protein_metadata": None}

        # Initialize metadata_payload to avoid UnboundLocalError
        metadata_payload: Dict[str, Any] = {
            "records": [],
            "columns": [],
            "shape": [0, 0],
            "dtypes": {},
            "source_counts": {},
            "query": protein_name,
            "sources": metadata_sources,
            "start_date": metadata_start,
            "end_date": metadata_end,
            "generated_at": datetime.utcnow().isoformat(),
        }
        total_results = 0
        source_counts: Dict[str, int] = {}

        if isinstance(metadata_df, pd.DataFrame) and not metadata_df.empty:
            total_results = len(metadata_df)
            if 'source' in metadata_df.columns:
                source_counts = metadata_df['source'].value_counts().to_dict()
            
            # Drop unnecessary columns to reduce memory usage before caching
            columns_to_drop = [
                'title_clean', 'authors_clean', 'abstract_clean', 'doi_clean', 'pmid_clean',
                'is_merged', 'merged_count', 'merge_type', 'original_indices', 'merge_timestamp',
                'match_type', 'match_value', 'enriched_fields', 'original_sources',
                'all_dois', 'all_pmids', 'deduplicated_at'
            ]
            # Only drop columns that actually exist in the DataFrame
            existing_columns_to_drop = [col for col in columns_to_drop if col in metadata_df.columns]
            if existing_columns_to_drop:
                metadata_df = metadata_df.drop(columns=existing_columns_to_drop)
                print(f"[metadata_fetcher_node] Dropped {len(existing_columns_to_drop)} unnecessary columns: {existing_columns_to_drop}")
            
            # Always store in external cache regardless of size
            # Generate unique cache_id using protein_name hash + timestamp
            protein_hash = hashlib.md5(protein_name.encode()).hexdigest()[:8]
            timestamp = int(time.time())
            cache_id = f"metadata_{protein_hash}_{timestamp}"
            
            # Add accession column to DataFrame before caching
            if accession:
                metadata_df["accession"] = accession
            else:
                metadata_df["accession"] = None
            
            # Store full data in cache (always executed, regardless of accession)
            full_metadata = {
                "records": metadata_df.to_dict(orient="records"),
                "columns": list(metadata_df.columns),
                "shape": list(metadata_df.shape),
                "dtypes": {col: str(dtype) for col, dtype in metadata_df.dtypes.items()},
                "source_counts": source_counts,
                "query": protein_name,
                "sources": metadata_sources,
                "start_date": metadata_start,
                "end_date": metadata_end,
                "generated_at": datetime.utcnow().isoformat(),
            }
            _metadata_cache[cache_id] = full_metadata
            
            # DEBUG: Log memory usage after caching
            df_size_mb = estimate_dataframe_size_mb(metadata_df)
            cache_size_mb = estimate_cache_size_mb()
            log_memory_usage(
                f"[metadata_fetcher_node] After caching (cache_id: {cache_id})",
                len(_metadata_cache)
            )
            print(f"[MEMORY DEBUG] DataFrame size: {df_size_mb:.2f}MB, "
                  f"Total cache size: {cache_size_mb:.2f}MB, "
                  f"Records cached: {len(full_metadata.get('records', []))}")
            
            # Cleanup: DataFrame is now in cache, can be deleted after CSV/DB operations
            
            # Store only reference + summary in state (always set, regardless of cache size)
            metadata_payload = {
                "cache_id": cache_id,  # Reference to cache
                "summary": {
                    "total_count": total_results,
                    "columns": list(metadata_df.columns),
                    "shape": list(metadata_df.shape),
                    "sources": source_counts,
                },
                "query": protein_name,
                "sources": metadata_sources,
                "start_date": metadata_start,
                "end_date": metadata_end,
                "generated_at": datetime.utcnow().isoformat(),
            }
            print(f"[metadata_fetcher_node] Metadata fetched successfully: {total_results} articles (stored in cache: {cache_id})")
            
            # Auto-cleanup if cache is getting large
            if len(_metadata_cache) > 10:
                print(f"[MEMORY DEBUG] Cache size ({len(_metadata_cache)}) exceeds threshold, running cleanup...")
                cleanup_stale_cache(max_age_seconds=1800)
                log_memory_usage("[metadata_fetcher_node] After auto-cleanup", len(_metadata_cache))
            
            # Save initial fetched metadata to CSV
            try:
                csv_dir = Path("metadata_exports")
                
                # Ensure directory exists with proper permissions
                if not csv_dir.exists():
                    csv_dir.mkdir(parents=True, mode=0o777)
                else:
                    # Try to fix permissions on existing directory
                    try:
                        os.chmod(csv_dir, 0o777)
                    except Exception as perm_error:
                        print(f"[metadata_fetcher_node] Warning: Could not change directory permissions: {perm_error}")
                
                safe_protein_name = re.sub(r'[^\w\s-]', '', protein_name).strip().replace(' ', '_')[:50]
                csv_filename = csv_dir / f"{safe_protein_name}_{timestamp}_01_fetched.csv"
                metadata_df.to_csv(csv_filename, index=False)
                
                # Try to set file permissions after creation
                try:
                    os.chmod(csv_filename, 0o666)
                except Exception as perm_error:
                    print(f"[metadata_fetcher_node] Warning: Could not set file permissions: {perm_error}")
                
                print(f"[metadata_fetcher_node] Saved {total_results} articles to CSV: {csv_filename}")
            except Exception as e:
                print(f"[metadata_fetcher_node] Error saving CSV: {e}")
            
            # Insert to database after CSV save
            try:
                conn = get_db_connection()
                insert_metadata_bulk(metadata_df, "metadata_fetched", conn)
                print(f"[metadata_fetcher_node] Saved {total_results} articles to database")
                conn.close()
            except Exception as e:
                print(f"[metadata_fetcher_node] Error saving to database: {e}")
            
            # Cleanup: Delete DataFrame after caching, CSV save, and DB insert
            del metadata_df
            print(f"[MEMORY DEBUG] Deleted DataFrame after caching and saving")
        else:
            metadata_payload = {
                "records": [],
                "columns": [],
                "shape": [0, 0],
                "dtypes": {},
                "source_counts": {},
                "query": protein_name,
                "sources": metadata_sources,
                "start_date": metadata_start,
                "end_date": metadata_end,
                "generated_at": datetime.utcnow().isoformat(),
            }
            print("[metadata_fetcher_node] Metadata DataFrame is empty")

        if total_results > 0:
            source_info = ""
            if source_counts:
                source_info = f" ({', '.join([f'{k}: {v}' for k, v in source_counts.items()])})"
            metadata_summary = (
                f"\n\n📚 **Research Metadata**: Found {total_results} articles "
                f"from PubMed and Semantic Scholar{source_info}"
            )
        else:
            metadata_summary = (
                f"\n\n📚 **Research Metadata**: No articles found for '{protein_name}'"
            )

        current_message = state.get("assistant_message", "")

        return {
            "protein_metadata": metadata_payload,
            "assistant_message": current_message + metadata_summary,
            "chat_history": [{"role": "assistant", "content": metadata_summary}],
        }

    # metadata_processor_node - Read from cache and convert to DataFrame
    async def metadata_processor_node(state: WorkflowState) -> Dict[str, Any]:
        """Read metadata from cache, convert to DataFrame, and process it."""
        print("\n[metadata_processor_node] Processing metadata...")
        
        metadata_payload = state.get("protein_metadata")
        if not metadata_payload:
            print("[metadata_processor_node] No metadata payload found in state")
            return {}
        
        # Track CSV path for PDF downloader node
        final_csv_path: Optional[str] = None
        
        # Metadata is always stored in cache now
        if "cache_id" not in metadata_payload:
            print("[metadata_processor_node] Error: cache_id not found in metadata_payload. Metadata should always be cached.")
            return {}
        
        cache_id = metadata_payload.get("cache_id")
        print(f"[metadata_processor_node] Retrieving metadata from cache: {cache_id}")
        
        metadata_df: Optional[pd.DataFrame] = None
        
        # Retrieve full data from cache
        cached_data = get_metadata_from_cache(cache_id)
        if cached_data and "records" in cached_data:
            log_memory_usage(f"[metadata_processor_node] Loading from cache: {cache_id}", len(_metadata_cache))
            # Convert cached records to DataFrame
            records = cached_data.get("records", [])
            columns = cached_data.get("columns", [])
            
            if records and columns:
                metadata_df = pd.DataFrame(records, columns=columns)
                log_memory_usage(f"[metadata_processor_node] After DataFrame creation ({len(metadata_df)} rows)", len(_metadata_cache))
                df_size_mb = estimate_dataframe_size_mb(metadata_df)
                print(f"[MEMORY DEBUG] Loaded DataFrame size: {df_size_mb:.2f}MB")
                print(f"[metadata_processor_node] Successfully loaded {len(metadata_df)} articles from cache")
            else:
                print("[metadata_processor_node] Cache data missing records or columns")
        else:
            print(f"[metadata_processor_node] Cache entry not found or invalid for cache_id: {cache_id}")
            return {}
        
        # Apply filtering if DataFrame is available
        if metadata_df is not None and not metadata_df.empty:
            original_count = len(metadata_df)
            print(f"\n[metadata_processor_node] Applying growth factor filter to {original_count} articles...")
            
            # Apply filter
            filtered_df = filter_growth_factors(metadata_df)
            filtered_count = len(filtered_df) if filtered_df is not None and not filtered_df.empty else 0
            
            print(f"[metadata_processor_node] Filtering complete: {original_count} → {filtered_count} articles")
            
            # Cleanup: Delete original DataFrame after filtering (will be replaced by filtered_df)
            del metadata_df
            
            # Save growth factor filtered results to CSV
            if filtered_df is not None and not filtered_df.empty:
                try:
                    cache_id = metadata_payload.get("cache_id", "unknown")
                    # Extract timestamp from cache_id (format: metadata_{hash}_{timestamp})
                    timestamp = cache_id.split("_")[-1] if "_" in cache_id else str(int(time.time()))
                    protein_name = metadata_payload.get("query", "unknown")
                    safe_protein_name = re.sub(r'[^\w\s-]', '', protein_name).strip().replace(' ', '_')[:50]
                    csv_dir = Path("metadata_exports")
                    
                    # Ensure directory exists with proper permissions
                    if not csv_dir.exists():
                        csv_dir.mkdir(parents=True, mode=0o777)
                    else:
                        # Try to fix permissions on existing directory
                        try:
                            os.chmod(csv_dir, 0o777)
                        except Exception as perm_error:
                            print(f"[metadata_processor_node] Warning: Could not change directory permissions: {perm_error}")
                    
                    csv_filename = csv_dir / f"{safe_protein_name}_{timestamp}_02_growth_factor_filtered.csv"
                    filtered_df.to_csv(csv_filename, index=False)
                    
                    # Try to set file permissions after creation
                    try:
                        os.chmod(csv_filename, 0o666)
                    except Exception as perm_error:
                        print(f"[metadata_processor_node] Warning: Could not set file permissions: {perm_error}")
                    
                    print(f"[metadata_processor_node] Saved {filtered_count} articles (after growth factor filter) to CSV: {csv_filename}")
                    log_memory_usage(f"[metadata_processor_node] After growth factor filter", len(_metadata_cache))
                except Exception as e:
                    print(f"[metadata_processor_node] Error saving growth factor filtered CSV: {e}")
                
                # Insert to database after CSV save
                try:
                    conn = get_db_connection()
                    insert_metadata_bulk(filtered_df, "metadata_growth_factor_filtered", conn)
                    print(f"[metadata_processor_node] Saved {filtered_count} articles to database (stage 2)")
                    conn.close()
                except Exception as e:
                    print(f"[metadata_processor_node] Error saving to database (stage 2): {e}")
            
            # Replace original DataFrame with filtered results
            # Note: metadata_df was already deleted above, so this creates a new reference
            metadata_df = filtered_df
            # Cleanup: filtered_df is now referenced by metadata_df, safe to delete after cache update
            
            # Update cache with filtered DataFrame
            cache_id = metadata_payload.get("cache_id")
            if cache_id and metadata_df is not None and not metadata_df.empty:
                    # Recalculate source counts for filtered data
                    filtered_source_counts = {}
                    if 'source' in metadata_df.columns:
                        filtered_source_counts = metadata_df['source'].value_counts().to_dict()
                    
                    # Update cache entry
                    updated_metadata = {
                        "records": metadata_df.to_dict(orient="records"),
                        "columns": list(metadata_df.columns),
                        "shape": list(metadata_df.shape),
                        "dtypes": {col: str(dtype) for col, dtype in metadata_df.dtypes.items()},
                        "source_counts": filtered_source_counts,
                        "query": metadata_payload.get("query", ""),
                        "sources": metadata_payload.get("sources", []),
                        "start_date": metadata_payload.get("start_date", ""),
                        "end_date": metadata_payload.get("end_date", ""),
                        "generated_at": metadata_payload.get("generated_at", datetime.utcnow().isoformat()),
                    }
                    _metadata_cache[cache_id] = updated_metadata
                    
                    # Update state payload summary
                    metadata_payload["summary"] = {
                        "total_count": filtered_count,
                        "columns": list(metadata_df.columns),
                        "shape": list(metadata_df.shape),
                        "sources": filtered_source_counts,
                    }
                    print(f"[metadata_processor_node] Updated cache entry: {cache_id}")
                
                # Cleanup: filtered_df is now in cache and referenced by metadata_df, can delete old reference
                # (metadata_df will be reused, so we keep it)
            
            # Print filtered DataFrame summary BEFORE parameter extraction
            if metadata_df is not None and not metadata_df.empty:
                print(f"\n{'='*80}")
                print(f"[metadata_processor_node] Filtered DataFrame Summary:")
                print(f"{'='*80}")
                print(f"Shape: {metadata_df.shape[0]} rows × {metadata_df.shape[1]} columns")
                print(f"\nColumns: {list(metadata_df.columns)}")
                
                # Print source breakdown if available
                if 'source' in metadata_df.columns:
                    source_counts = metadata_df['source'].value_counts()
                    print(f"\nSource breakdown:")
                    for source, count in source_counts.items():
                        print(f"  - {source}: {count} articles")
                
                # Print growth factor breakdown if available
                if 'Growth_Factor_Name' in metadata_df.columns:
                    gf_counts = metadata_df['Growth_Factor_Name'].value_counts()
                    print(f"\nGrowth Factor breakdown:")
                    for gf, count in gf_counts.items():
                        print(f"  - {gf}: {count} articles")
                
                # Print first few rows
                # print(f"\nFirst 5 rows:")
                # print(metadata_df.head().to_string())
                
                print(f"{'='*80}\n")
            
            # Extract parameters using LLM if not already extracted
            if metadata_df is not None and not metadata_df.empty:
                # Check if parameters already extracted
                has_q1_column = "Q1_Protein_Production" in metadata_df.columns
                q1_all_na = metadata_df["Q1_Protein_Production"].isna().all() if has_q1_column else True
                q1_all_empty = (metadata_df["Q1_Protein_Production"] == "").all() if has_q1_column else True
                
                print(f"[metadata_processor_node] Parameter extraction check: has_q1_column={has_q1_column}, q1_all_na={q1_all_na}, q1_all_empty={q1_all_empty}")
                
                if not has_q1_column or q1_all_na or q1_all_empty:
                    print(f"\n[metadata_processor_node] Starting LLM parameter extraction for {len(metadata_df)} articles...")
                    try:
                        # Extract parameters asynchronously
                        # Note: This creates a new DataFrame, old one will be garbage collected
                        old_metadata_df = metadata_df
                        metadata_df = await extract_parameters_from_dataframe(metadata_df, start_idx=0)
                        # Cleanup: Delete old DataFrame after extraction
                        del old_metadata_df
                        print(f"[metadata_processor_node] Parameter extraction complete")
                        
                        # Save parameter extraction results to CSV
                        if metadata_df is not None and not metadata_df.empty:
                            try:
                                cache_id = metadata_payload.get("cache_id", "unknown")
                                # Extract timestamp from cache_id (format: metadata_{hash}_{timestamp})
                                timestamp = cache_id.split("_")[-1] if "_" in cache_id else str(int(time.time()))
                                protein_name = metadata_payload.get("query", "unknown")
                                safe_protein_name = re.sub(r'[^\w\s-]', '', protein_name).strip().replace(' ', '_')[:50]
                                csv_dir = Path("metadata_exports")
                                
                                # Ensure directory exists with proper permissions
                                if not csv_dir.exists():
                                    csv_dir.mkdir(parents=True, mode=0o777)
                                else:
                                    # Try to fix permissions on existing directory
                                    try:
                                        os.chmod(csv_dir, 0o777)
                                    except Exception as perm_error:
                                        print(f"[metadata_processor_node] Warning: Could not change directory permissions: {perm_error}")
                                
                                csv_filename = csv_dir / f"{safe_protein_name}_{timestamp}_03_parameters_extracted.csv"
                                metadata_df.to_csv(csv_filename, index=False)
                                
                                # Try to set file permissions after creation
                                try:
                                    os.chmod(csv_filename, 0o666)
                                except Exception as perm_error:
                                    print(f"[metadata_processor_node] Warning: Could not set file permissions: {perm_error}")
                                
                                print(f"[metadata_processor_node] Saved {len(metadata_df)} articles (after parameter extraction) to CSV: {csv_filename}")
                                log_memory_usage(f"[metadata_processor_node] After parameter extraction", len(_metadata_cache))
                            except Exception as e:
                                print(f"[metadata_processor_node] Error saving parameter extraction CSV: {e}")
                            
                            # Insert to database after CSV save
                            try:
                                conn = get_db_connection()
                                insert_metadata_bulk(metadata_df, "metadata_parameters_extracted", conn)
                                print(f"[metadata_processor_node] Saved {len(metadata_df)} articles to database (stage 3)")
                                conn.close()
                            except Exception as e:
                                print(f"[metadata_processor_node] Error saving to database (stage 3): {e}")
                        
                        # Update cache with extracted parameters
                            cache_id = metadata_payload.get("cache_id")
                            if cache_id and metadata_df is not None and not metadata_df.empty:
                                filtered_source_counts = {}
                                if 'source' in metadata_df.columns:
                                    filtered_source_counts = metadata_df['source'].value_counts().to_dict()
                                
                                # Update cache entry
                                updated_metadata = {
                                    "records": metadata_df.to_dict(orient="records"),
                                    "columns": list(metadata_df.columns),
                                    "shape": list(metadata_df.shape),
                                    "dtypes": {col: str(dtype) for col, dtype in metadata_df.dtypes.items()},
                                    "source_counts": filtered_source_counts,
                                    "query": metadata_payload.get("query", ""),
                                    "sources": metadata_payload.get("sources", []),
                                    "start_date": metadata_payload.get("start_date", ""),
                                    "end_date": metadata_payload.get("end_date", ""),
                                    "generated_at": metadata_payload.get("generated_at", datetime.utcnow().isoformat()),
                                }
                                _metadata_cache[cache_id] = updated_metadata
                                
                                # Update state payload summary
                                metadata_payload["summary"] = {
                                    "total_count": len(metadata_df),
                                    "columns": list(metadata_df.columns),
                                    "shape": list(metadata_df.shape),
                                    "sources": filtered_source_counts,
                                }
                                print(f"[metadata_processor_node] Updated cache entry with extracted parameters: {cache_id}")
                    except Exception as e:
                        print(f"[metadata_processor_node] Error during parameter extraction: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"[metadata_processor_node] Parameters already extracted, skipping LLM extraction")
            
            # Cleanup: After parameter extraction, old DataFrame references are cleared
            
            # Apply final filtering (Human + E. coli + Growth Factor Pattern Match)
            if metadata_df is not None and not metadata_df.empty:
                # Check if required columns exist for final filtering
                required_cols = ['Q3_Recombinant_Proteins', 'Q4_Species', 'Q5_Host_Organism', 'Growth_Factor_Name']
                has_required_cols = all(col in metadata_df.columns for col in required_cols)
                
                if has_required_cols:
                    pre_final_count = len(metadata_df)
                    print(f"\n[metadata_processor_node] Applying final filter (Human + E. coli + Growth Factor Pattern Match)...")
                    print(f"[metadata_processor_node] Articles before final filter: {pre_final_count}")
                    
                    try:
                        # Apply final filtering
                        # Note: This creates a new DataFrame, old one will be garbage collected
                        old_metadata_df = metadata_df
                        metadata_df = filter_human_ecoli_gf(metadata_df)
                        # Cleanup: Delete old DataFrame after filtering
                        del old_metadata_df
                        final_count = len(metadata_df) if metadata_df is not None and not metadata_df.empty else 0
                        print(f"[metadata_processor_node] Final filtering complete: {pre_final_count} → {final_count} articles")
                        
                        # Save final filtered results to CSV
                        final_csv_path = None  # Initialize to None (will be set in outer scope)
                        if metadata_df is not None and not metadata_df.empty:
                            try:
                                cache_id = metadata_payload.get("cache_id", "unknown")
                                # Extract timestamp from cache_id (format: metadata_{hash}_{timestamp})
                                timestamp = cache_id.split("_")[-1] if "_" in cache_id else str(int(time.time()))
                                protein_name = metadata_payload.get("query", "unknown")
                                safe_protein_name = re.sub(r'[^\w\s-]', '', protein_name).strip().replace(' ', '_')[:50]
                                csv_dir = Path("metadata_exports")
                                
                                # Ensure directory exists with proper permissions
                                if not csv_dir.exists():
                                    csv_dir.mkdir(parents=True, mode=0o777)
                                else:
                                    # Try to fix permissions on existing directory
                                    try:
                                        os.chmod(csv_dir, 0o777)
                                    except Exception as perm_error:
                                        print(f"[metadata_processor_node] Warning: Could not change directory permissions: {perm_error}")
                                
                                # Add "pdf found" column set to False for all rows
                                if "pdf found" not in metadata_df.columns:
                                    metadata_df["pdf found"] = False
                                
                                csv_filename = csv_dir / f"{safe_protein_name}_{timestamp}_04_final_filtered.csv"
                                metadata_df.to_csv(csv_filename, index=False)
                                
                                # Try to set file permissions after creation
                                try:
                                    os.chmod(csv_filename, 0o666)
                                except Exception as perm_error:
                                    print(f"[metadata_processor_node] Warning: Could not set file permissions: {perm_error}")
                                
                                final_csv_path = str(csv_filename)
                                print(f"[metadata_processor_node] Saved {final_count} articles (after final filter) to CSV: {csv_filename}")
                                log_memory_usage(f"[metadata_processor_node] After final filter", len(_metadata_cache))
                            except Exception as e:
                                print(f"[metadata_processor_node] Error saving final filtered CSV: {e}")
                                # Don't set final_csv_path if CSV save fails - let pdf_downloader_node handle the error
                                final_csv_path = None
                            
                            # Insert to database after CSV save
                            try:
                                conn = get_db_connection()
                                insert_metadata_bulk(metadata_df, "metadata_final_filtered", conn)
                                print(f"[metadata_processor_node] Saved {final_count} articles to database (stage 4)")
                                conn.close()
                            except Exception as e:
                                print(f"[metadata_processor_node] Error saving to database (stage 4): {e}")
                        
                        # Update cache with final filtered DataFrame
                            cache_id = metadata_payload.get("cache_id")
                            if cache_id and metadata_df is not None and not metadata_df.empty:
                                final_source_counts = {}
                                if 'source' in metadata_df.columns:
                                    final_source_counts = metadata_df['source'].value_counts().to_dict()
                                
                                # Update cache entry
                                updated_metadata = {
                                    "records": metadata_df.to_dict(orient="records"),
                                    "columns": list(metadata_df.columns),
                                    "shape": list(metadata_df.shape),
                                    "dtypes": {col: str(dtype) for col, dtype in metadata_df.dtypes.items()},
                                    "source_counts": final_source_counts,
                                    "query": metadata_payload.get("query", ""),
                                    "sources": metadata_payload.get("sources", []),
                                    "start_date": metadata_payload.get("start_date", ""),
                                    "end_date": metadata_payload.get("end_date", ""),
                                    "generated_at": metadata_payload.get("generated_at", datetime.utcnow().isoformat()),
                                }
                                _metadata_cache[cache_id] = updated_metadata
                                
                                # Update state payload summary
                                metadata_payload["summary"] = {
                                    "total_count": final_count,
                                    "columns": list(metadata_df.columns),
                                    "shape": list(metadata_df.shape),
                                    "sources": final_source_counts,
                                }
                                print(f"[metadata_processor_node] Updated cache entry with final filtered data: {cache_id}")
                    except Exception as e:
                        print(f"[metadata_processor_node] Error during final filtering: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    missing_cols = [col for col in required_cols if col not in metadata_df.columns]
                    print(f"[metadata_processor_node] Skipping final filter: missing required columns: {missing_cols}")
            
            # Print DataFrame information AFTER parameter extraction and final filtering
            if metadata_df is not None and not metadata_df.empty:
                print(f"\n{'='*80}")
                print(f"[metadata_processor_node] Final DataFrame Summary (After All Processing):")
                print(f"{'='*80}")
                print(f"Shape: {metadata_df.shape[0]} rows × {metadata_df.shape[1]} columns")
                print(f"\nColumns: {list(metadata_df.columns)}")
                
                # Print source breakdown if available
                if 'source' in metadata_df.columns:
                    source_counts = metadata_df['source'].value_counts()
                    print(f"\nSource breakdown:")
                    for source, count in source_counts.items():
                        print(f"  - {source}: {count} articles")
                
                # Print growth factor breakdown if available
                if 'Growth_Factor_Name' in metadata_df.columns:
                    gf_counts = metadata_df['Growth_Factor_Name'].value_counts()
                    print(f"\nGrowth Factor breakdown:")
                    for gf, count in gf_counts.items():
                        print(f"  - {gf}: {count} articles")
                
                # Print parameter extraction statistics if available
                if 'Q1_Protein_Production' in metadata_df.columns:
                    q1_counts = metadata_df['Q1_Protein_Production'].value_counts()
                    print(f"\nParameter Extraction Statistics:")
                    print(f"  - Total articles processed: {len(metadata_df)}")
                    print(f"  - Q1_Protein_Production breakdown:")
                    for value, count in q1_counts.items():
                        if pd.notna(value) and str(value).strip():
                            print(f"    - {value}: {count} articles")
                
                # Print final filter statistics if available
                if 'Q4_Species' in metadata_df.columns and 'Q5_Host_Organism' in metadata_df.columns:
                    print(f"\nFinal Filter Statistics:")
                    if 'Q4_Species' in metadata_df.columns:
                        species_counts = metadata_df['Q4_Species'].value_counts()
                        print(f"  - Species breakdown:")
                        for species, count in species_counts.items():
                            if pd.notna(species) and str(species).strip():
                                print(f"    - {species}: {count} articles")
                    if 'Q5_Host_Organism' in metadata_df.columns:
                        host_counts = metadata_df['Q5_Host_Organism'].value_counts()
                        print(f"  - Host Organism breakdown:")
                        for host, count in host_counts.items():
                            if pd.notna(host) and str(host).strip():
                                print(f"    - {host}: {count} articles")
                
                # Print data types
                print(f"\nData types:")
                print(metadata_df.dtypes.to_string())
                
                # Print sample of key columns if they exist (including extracted parameters)
                key_columns = ['title', 'abstract', 'authors', 'journal', 'doi', 'publication year', 'Growth_Factor_Name', 'Q1_Protein_Production', 'Q3_Recombinant_Proteins', 'Q4_Species', 'Q5_Host_Organism']
                available_key_columns = [col for col in key_columns if col in metadata_df.columns]
                # if available_key_columns:
                    # print(f"\nSample data from key columns (including extracted parameters):")
                    # print(metadata_df[available_key_columns].head(3).to_string())
                
                print(f"{'='*80}\n")
        else:
            print("[metadata_processor_node] DataFrame is empty or None after filtering")
        
        # Return updated metadata payload and CSV path to update state
        result = {"protein_metadata": metadata_payload}
        if final_csv_path:
            result["final_filtered_csv_path"] = final_csv_path
        
        # Final cleanup: Delete DataFrame at end of node (data is in cache and CSV/DB)
        # Note: metadata_df may have been deleted earlier in the processing pipeline
        try:
            if metadata_df is not None:
                del metadata_df
                print(f"[MEMORY DEBUG] Deleted DataFrame at end of metadata_processor_node")
        except (NameError, UnboundLocalError):
            # DataFrame already deleted or never created - this is fine
            pass
        
        return result

    # pdf_downloader_node - Upload final filtered CSV to PDF downloader API
    async def pdf_downloader_node(state: WorkflowState) -> Dict[str, Any]:
        """Upload the final filtered CSV file to the PDF downloader API."""
        print("\n[pdf_downloader_node] Uploading CSV to PDF downloader API...")
        
        csv_path = state.get("final_filtered_csv_path")
        if not csv_path:
            error_msg = "[pdf_downloader_node] Error: No CSV path found in state. Cannot upload to PDF downloader API."
            print(error_msg)
            raise ValueError(error_msg)
        
        # Check if file exists
        csv_file = Path(csv_path)
        if not csv_file.exists():
            error_msg = f"[pdf_downloader_node] Error: CSV file not found at path: {csv_path}"
            print(error_msg)
            raise FileNotFoundError(error_msg)
        
        # Get API URL from environment variable
        api_url = os.getenv("PDF_DOWNLOADER_API_URL", "http://host.docker.internal:8003/upload-csv")
        print(f"[pdf_downloader_node] API URL: {api_url}")
        print(f"[pdf_downloader_node] Uploading CSV file: {csv_path}")
        
        # Track verified folder path and verified CSV path from API response
        verified_folder_path: Optional[str] = None
        verified_csv_path: Optional[str] = None
        
        try:
            # Import requests (already available via LLMClient, but import directly for clarity)
            import requests
            
            # First, check if API is reachable via health endpoint
            health_url = api_url.replace("/upload-csv", "/health")
            try:
                health_response = requests.get(health_url, timeout=5)
                print(f"[pdf_downloader_node] Health check: {health_response.status_code} - {health_response.text[:100]}")
            except Exception as health_e:
                print(f"[pdf_downloader_node] Warning: Health check failed: {health_e}")
                print(f"[pdf_downloader_node] Proceeding with upload anyway...")
            
            # Get file size for logging
            file_size_mb = csv_file.stat().st_size / (1024 * 1024)
            print(f"[pdf_downloader_node] CSV file size: {file_size_mb:.2f} MB")
            
            # Upload CSV file
            # Use longer timeout since PDF downloader may take time to process and download PDFs
            print(f"[pdf_downloader_node] Starting upload (timeout: 600s)...")
            with open(csv_file, 'rb') as f:
                files = {'file': (csv_file.name, f, 'text/csv')}
                response = requests.post(api_url, files=files, timeout=600)  # 10 minutes timeout
            print(f"[pdf_downloader_node] Upload completed, response status: {response.status_code}")
            
            # Check response status
            response.raise_for_status()
            
            # Parse and log JSON response
            try:
                response_data = response.json()
                print(f"\n[pdf_downloader_node] API Response:")
                print(f"  Message: {response_data.get('message', 'N/A')}")
                print(f"  Total rows: {response_data.get('total_rows', 'N/A')}")
                print(f"  PDFs found: {response_data.get('pdfs_found', 'N/A')}")
                print(f"  Session ID: {response_data.get('session_id', 'N/A')}")
                
                output_files = response_data.get('output_files', {})
                if output_files:
                    print(f"  Output files:")
                    verified_csv_rel = output_files.get('verified_csv', '')
                    print(f"    Verified CSV: {verified_csv_rel}")
                    verified_folder = output_files.get('verified_folder', '')
                    print(f"    Verified folder: {verified_folder}")
                    
                    # Construct full paths
                    base_path = os.getenv("PDF_DOWNLOADER_BASE_PATH", "/app/pdfdownloader")
                    
                    if verified_csv_rel:
                        verified_csv_path = str(Path(base_path) / verified_csv_rel)
                        print(f"[pdf_downloader_node] Verified CSV path: {verified_csv_path}")
                    
                    if verified_folder:
                        verified_folder_path = str(Path(base_path) / verified_folder)
                        print(f"[pdf_downloader_node] Verified folder path: {verified_folder_path}")
                
                print(f"[pdf_downloader_node] CSV uploaded successfully to PDF downloader API")
            except (ValueError, KeyError) as e:
                print(f"[pdf_downloader_node] Warning: Could not parse JSON response: {e}")
                print(f"[pdf_downloader_node] Raw response: {response.text[:500]}")
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"[pdf_downloader_node] Connection error: API may not be running or unreachable: {e}"
            print(error_msg)
            print(f"[pdf_downloader_node] Check if PDF downloader API is running on {api_url}")
            raise RuntimeError(error_msg) from e
        except requests.exceptions.Timeout as e:
            error_msg = f"[pdf_downloader_node] Request timeout: API took longer than 600s to respond: {e}"
            print(error_msg)
            print(f"[pdf_downloader_node] The API may still be processing. Check API logs for status.")
            raise RuntimeError(error_msg) from e
        except requests.exceptions.RequestException as e:
            error_msg = f"[pdf_downloader_node] Error uploading CSV to API: {type(e).__name__}: {e}"
            print(error_msg)
            if hasattr(e, 'response') and e.response is not None:
                print(f"[pdf_downloader_node] Response status: {e.response.status_code}")
                print(f"[pdf_downloader_node] Response body: {e.response.text[:500]}")
            else:
                print(f"[pdf_downloader_node] No response received (connection may have been closed)")
            raise RuntimeError(error_msg) from e
        except Exception as e:
            error_msg = f"[pdf_downloader_node] Unexpected error during CSV upload: {e}"
            print(error_msg)
            raise RuntimeError(error_msg) from e
        
        # Update pdf_found column in database using verified CSV
        if verified_csv_path and os.path.exists(verified_csv_path):
            try:
                conn = get_db_connection()
                updated_count = update_pdf_found_from_verified_csv(verified_csv_path, conn)
                print(f"[pdf_downloader_node] Updated {updated_count} rows in metadata_final_filtered with pdf_found status")
                conn.close()
            except Exception as e:
                print(f"[pdf_downloader_node] Error updating database: {e}")
                # Don't fail the node if database update fails
        else:
            if verified_csv_path:
                print(f"[pdf_downloader_node] Warning: Verified CSV not found at {verified_csv_path}, skipping database update")
            else:
                print(f"[pdf_downloader_node] Warning: No verified CSV path in API response, skipping database update")
        
        # Return verified folder path for next node
        result = {}
        if verified_folder_path:
            result["pdf_verified_folder_path"] = verified_folder_path
        return result

    # pdf_processor_node - Process PDFs through GROBID extraction API
    async def pdf_processor_node(state: WorkflowState) -> Dict[str, Any]:
        """Execute process_folder.sh script to batch process PDFs through GROBID extraction API."""
        print("\n[pdf_processor_node] Starting PDF processing through GROBID extraction...")
        
        folder_path = state.get("pdf_verified_folder_path")
        if not folder_path:
            error_msg = "[pdf_processor_node] Error: No verified folder path found in state. Cannot process PDFs."
            print(error_msg)
            raise ValueError(error_msg)
        
        # Validate folder exists
        # Note: If the PDF downloader API container doesn't have the volume mount,
        # the folder won't be accessible. The API container needs to be restarted with:
        # docker run -d --name predictabio-api-test -p 8003:8000 \
        #   -v /Users/eesitasen/Desktop/predictabio/pdfdownloader:/app/pdfdownloader \
        #   -e PDF_DOWNLOADER_BASE_PATH=/app/pdfdownloader predictabio-api
        folder = Path(folder_path)
        if not folder.exists():
            # Check if it's a volume mount issue - list what's actually in the base directory
            base_path = Path(folder_path).parent.parent  # Go up to /app/pdfdownloader
            if base_path.exists():
                print(f"[pdf_processor_node] Base path exists: {base_path}")
                try:
                    contents = list(base_path.iterdir())[:10]
                    print(f"[pdf_processor_node] Contents of base path: {[str(p.name) for p in contents]}")
                    # Check if temp_* folders exist but in wrong location
                    temp_folders = [p for p in contents if p.name.startswith('temp_')]
                    if temp_folders:
                        print(f"[pdf_processor_node] Found temp folders in base path: {[str(p.name) for p in temp_folders]}")
                        print(f"[pdf_processor_node] Note: PDF downloader API may need to be restarted with volume mount")
                except Exception as e:
                    print(f"[pdf_processor_node] Error listing base path contents: {e}")
            else:
                print(f"[pdf_processor_node] Base path does not exist: {base_path}")
            error_msg = f"[pdf_processor_node] Error: Folder not found: {folder_path}"
            print(error_msg)
            print(f"[pdf_processor_node] This usually means the PDF downloader API container needs to be restarted with a volume mount.")
            print(f"[pdf_processor_node] Run: /Users/eesitasen/Desktop/predictabio/pdfdownloader/restart_api_container.sh")
            raise FileNotFoundError(error_msg)
        
        if not folder.is_dir():
            error_msg = f"[pdf_processor_node] Error: Path exists but is not a directory: {folder_path}"
            print(error_msg)
            raise FileNotFoundError(error_msg)
        
        print(f"[pdf_processor_node] Folder verified: {folder_path}")
        
        # Get script path from environment variable or use default
        script_path = os.getenv(
            "GROBID_PROCESSOR_SCRIPT_PATH",
            "/app/grobid_extractor/process_folder.sh"
        )
        
        # Validate script exists
        script_file = Path(script_path)
        if not script_file.exists():
            error_msg = f"[pdf_processor_node] Error: Script not found at: {script_path}"
            print(error_msg)
            raise FileNotFoundError(error_msg)
        
        # Make script executable if needed
        if not os.access(script_path, os.X_OK):
            os.chmod(script_path, 0o755)
        
        print(f"[pdf_processor_node] Script path: {script_path}")
        print(f"[pdf_processor_node] Processing folder: {folder_path}")
        
        try:
            # Execute the script with environment variables for GROBID API
            print(f"[pdf_processor_node] Executing script...")
            env = os.environ.copy()
            # Set GROBID API configuration if not already set
            if "GROBID_API_HOST" not in env:
                env["GROBID_API_HOST"] = os.getenv("GROBID_API_HOST", "host.docker.internal")
            if "GROBID_API_PORT" not in env:
                env["GROBID_API_PORT"] = os.getenv("GROBID_API_PORT", "9000")
            
            result = subprocess.run(
                [script_path, folder_path],
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour max timeout
                check=False,  # We'll check the exit code manually
                env=env  # Pass environment variables to the script
            )
            
            # Log script output
            if result.stdout:
                print(f"[pdf_processor_node] Script stdout:")
                print(result.stdout)
            if result.stderr:
                print(f"[pdf_processor_node] Script stderr:")
                print(result.stderr)
            
            # Check exit code
            if result.returncode != 0:
                error_msg = f"[pdf_processor_node] Script failed with exit code {result.returncode}"
                print(error_msg)
                if result.stderr:
                    print(f"[pdf_processor_node] Error output: {result.stderr[:500]}")
                raise RuntimeError(error_msg)
            
            # Parse script output to extract statistics
            output_text = result.stdout
            processing_results: Dict[str, Any] = {
                "success_count": 0,
                "failed_count": 0,
                "total_count": 0,
                "failed_files": [],
                "output_path": ""
            }
            
            # Extract success count: "✅ Successful: X/Y"
            success_match = re.search(r'✅\s*Successful:\s*(\d+)/(\d+)', output_text)
            if success_match:
                processing_results["success_count"] = int(success_match.group(1))
                processing_results["total_count"] = int(success_match.group(2))
            
            # Extract failed count: "❌ Failed: X/Y"
            failed_match = re.search(r'❌\s*Failed:\s*(\d+)/(\d+)', output_text)
            if failed_match:
                processing_results["failed_count"] = int(failed_match.group(1))
                if processing_results["total_count"] == 0:
                    processing_results["total_count"] = int(failed_match.group(2))
            
            # Extract output path: "💾 All outputs saved to: ..."
            output_match = re.search(r'💾\s*All outputs saved to:\s*(.+)', output_text)
            if output_match:
                processing_results["output_path"] = output_match.group(1).strip()
            
            # Extract failed files list
            failed_files_section = False
            for line in output_text.split('\n'):
                if '❌ Failed files:' in line:
                    failed_files_section = True
                    continue
                if failed_files_section and line.strip().startswith('- '):
                    failed_file = line.strip()[2:].strip()
                    if failed_file:
                        processing_results["failed_files"].append(failed_file)
                elif failed_files_section and line.strip() and not line.strip().startswith('- '):
                    # End of failed files section
                    break
            
            print(f"\n[pdf_processor_node] Processing Summary:")
            print(f"  Total PDFs: {processing_results['total_count']}")
            print(f"  Successful: {processing_results['success_count']}")
            print(f"  Failed: {processing_results['failed_count']}")
            if processing_results['failed_files']:
                print(f"  Failed files: {len(processing_results['failed_files'])}")
            if processing_results['output_path']:
                print(f"  Output path: {processing_results['output_path']}")
            
            # If there are failures, log them but don't stop workflow (as per plan, we stop on script failure, not on individual PDF failures)
            if processing_results['failed_count'] > 0:
                print(f"[pdf_processor_node] Warning: {processing_results['failed_count']} PDF(s) failed to process")
            
            print(f"[pdf_processor_node] PDF processing completed successfully")
            
            return {"pdf_processing_results": processing_results}
            
        except subprocess.TimeoutExpired:
            error_msg = "[pdf_processor_node] Error: Script execution timed out after 1 hour"
            print(error_msg)
            raise RuntimeError(error_msg)
        except FileNotFoundError as e:
            error_msg = f"[pdf_processor_node] Error: Script or folder not found: {e}"
            print(error_msg)
            raise
        except Exception as e:
            error_msg = f"[pdf_processor_node] Unexpected error during PDF processing: {e}"
            print(error_msg)
            raise RuntimeError(error_msg) from e

    # methods_validator_node - Validate and classify research papers for protein production
    async def methods_validator_node(state: WorkflowState) -> Dict[str, Any]:
        """Process JSON files from GROBID output to validate and classify recombinant protein production."""
        print("\n[methods_validator_node] Starting methods validation and classification...")
        
        # Get input directory from environment variable
        input_dir = os.getenv(
            "GROBID_OUTPUT_DIR",
            "/app/grobid_extractor/app/outputs"
        )
        input_path = Path(input_dir)
        
        # Validate input directory exists
        if not input_path.exists() or not input_path.is_dir():
            error_msg = f"[methods_validator_node] Error: Input directory not found or is not a directory: {input_dir}"
            print(error_msg)
            raise FileNotFoundError(error_msg)
        
        print(f"[methods_validator_node] Input directory: {input_dir}")
        
        # Add script directory to Python path
        script_dir = os.getenv(
            "METHODS_VALIDATOR_SCRIPT_DIR",
            "/app/filter1_2/filter_2"
        )
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
        
        # Import functions from the validation script
        try:
            from run_validate_methods_local import (
                list_local_json_files,
                read_local_json,
                extract_structured_text,
                classify_concatenated_methods,
                create_summary_dataframe
            )
            print(f"[methods_validator_node] Successfully imported validation functions")
        except ImportError as e:
            error_msg = f"[methods_validator_node] Error: Failed to import validation functions: {e}"
            print(error_msg)
            raise ImportError(error_msg) from e
        
        # List JSON files
        print(f"[methods_validator_node] Listing JSON files from {input_dir}...")
        json_files = list_local_json_files(input_path)
        
        if not json_files:
            error_msg = f"[methods_validator_node] Error: No JSON files found in {input_dir}"
            print(error_msg)
            raise ValueError(error_msg)
        
        print(f"[methods_validator_node] Found {len(json_files)} JSON files to process")
        
        # Process each file
        all_results = []
        error_log = []
        
        for file_path in json_files:
            try:
                print(f"[methods_validator_node] Processing: {file_path.name}")
                
                # Read JSON file
                data = read_local_json(file_path)
                
                # Extract structured text
                combined_text = extract_structured_text(data)
                
                if not combined_text:
                    result = {
                        "file": file_path.name,
                        "file_path": str(file_path),
                        "combined_methods_length": 0,
                        "classification": {
                            "answer": "insufficient_data",
                            "confidence": 0.0,
                            "reason": "Empty or missing methods/results sections.",
                            "recombinant_protein_count": 0,
                            "recombinant_proteins": [],
                            "expression_hosts": [],
                            "host_details": {}
                        },
                        "raw_response": ""
                    }
                    all_results.append(result)
                    error_log.append({
                        "file": result["file"],
                        "error_type": result["classification"]["answer"],
                        "reason": result["classification"]["reason"]
                    })
                    continue
                
                # Classify using LLM
                classification_result = classify_concatenated_methods(combined_text)
                classification = classification_result["parsed"]
                raw_response = classification_result["raw"]
                
                # Process and structure protein data
                proteins = classification.get("recombinant_proteins", [])
                structured_proteins = []
                if proteins:
                    for protein in proteins:
                        if isinstance(protein, str):
                            structured_proteins.append({
                                "name": protein,
                                "species": "unknown",
                                "tags": "",
                                "construct_details": ""
                            })
                        elif isinstance(protein, dict):
                            if "species" not in protein:
                                protein["species"] = "unknown"
                            structured_proteins.append(protein)
                
                classification["recombinant_proteins"] = structured_proteins
                classification["recombinant_protein_count"] = len(structured_proteins)
                
                # Deduplicate hosts
                hosts = classification.get("expression_hosts", [])
                unique_hosts = list(set(hosts)) if hosts else []
                classification["expression_hosts"] = unique_hosts
                
                # Ensure protein_host_pairs exists
                classification.setdefault("protein_host_pairs", [])
                
                result = {
                    "file": file_path.name,
                    "file_path": str(file_path),
                    "combined_methods_length": len(combined_text),
                    "classification": classification,
                    "raw_response": raw_response
                }
                all_results.append(result)
                
                if classification["answer"] in ["error", "insufficient_data"]:
                    error_log.append({
                        "file": result["file"],
                        "error_type": classification["answer"],
                        "reason": classification["reason"],
                        "raw_response": raw_response
                    })
                
            except Exception as e:
                print(f"[methods_validator_node] Error processing {file_path.name}: {e}")
                error_log.append({
                    "file": file_path.name,
                    "error_type": "error",
                    "reason": f"Exception: {e}"
                })
                all_results.append({
                    "file": file_path.name,
                    "file_path": str(file_path),
                    "combined_methods_length": 0,
                    "classification": {
                        "answer": "error",
                        "confidence": 0.0,
                        "reason": f"Exception: {e}",
                        "recombinant_proteins": [],
                        "expression_hosts": [],
                        "protein_host_pairs": []
                    },
                    "raw_response": ""
                })
        
        # Create summary DataFrame
        print(f"[methods_validator_node] Creating summary DataFrame...")
        df = create_summary_dataframe(all_results)
        
        # Save outputs
        output_dir = Path("metadata_exports")
        output_dir.mkdir(exist_ok=True)
        
        output_json_path = output_dir / "methods_classification_clean.json"
        output_csv_path = output_dir / "methods_classification_summary.csv"
        error_log_path = output_dir / "methods_classification_error_log.json"
        
        # Save JSON results
        with open(output_json_path, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"[methods_validator_node] Saved JSON results to: {output_json_path}")
        
        # Save CSV summary
        df.to_csv(output_csv_path, index=False)
        print(f"[methods_validator_node] Saved CSV summary to: {output_csv_path}")
        
        # Update methods_validation_status in database
        try:
            conn = get_db_connection()
            updated_count = update_methods_validation_from_csv(str(output_csv_path), conn)
            print(f"[methods_validator_node] Updated {updated_count} rows in metadata_final_filtered with methods_validation_status")
            conn.close()
        except Exception as e:
            print(f"[methods_validator_node] Error updating database: {e}")
            # Don't fail the node if database update fails
        
        # Save error log if any
        if error_log:
            with open(error_log_path, "w") as f:
                json.dump(error_log, f, indent=2)
            print(f"[methods_validator_node] Saved error log to: {error_log_path}")
        
        # Calculate summary statistics
        total_files = len(all_results)
        yes_count = sum(1 for r in all_results if r["classification"]["answer"] == "yes")
        no_count = sum(1 for r in all_results if r["classification"]["answer"] == "no")
        error_count = len(error_log)
        
        validation_results = {
            "total_files": total_files,
            "yes_count": yes_count,
            "no_count": no_count,
            "error_count": error_count,
            "output_json_path": str(output_json_path),
            "output_csv_path": str(output_csv_path),
            "error_log_path": str(error_log_path) if error_log else None
        }
        
        print(f"\n[methods_validator_node] Validation Summary:")
        print(f"  Total files processed: {total_files}")
        print(f"  Recombinant protein production: {yes_count}")
        print(f"  No recombinant production: {no_count}")
        print(f"  Errors/Insufficient data: {error_count}")
        print(f"[methods_validator_node] Methods validation completed successfully")
        
        return {"methods_validation_results": validation_results}

    # Build workflow graph
    graph = StateGraph(WorkflowState)
    graph.add_node("entity_extraction", entity_extraction_node)
    graph.add_node("entity_clarification", entity_clarification_node)
    graph.add_node("dynamic_search", dynamic_search_node)
    graph.add_node("retry_node", retry_node)
    graph.add_node("narrow_down_node", narrow_down_node)
    graph.add_node("search_clarification", search_clarification_node)
    graph.add_node("select_node", select_node)
    graph.add_node("protein_details_node", protein_details_node)
    graph.add_node("metadata_fetcher_node", metadata_fetcher_node)
    graph.add_node("metadata_processor_node", metadata_processor_node)
    graph.add_node("pdf_downloader_node", pdf_downloader_node)
    graph.add_node("pdf_processor_node", pdf_processor_node)
    graph.add_node("methods_validator_node", methods_validator_node)

    graph.add_edge(START, "entity_extraction")

    # Conditional transitions
    def route_from_extraction(state: WorkflowState) -> str:
        if state.get("protein_accession") or (state.get("protein_name") and state.get("organism")):
            return "dynamic_search"
        return "entity_clarification"

    def route_from_search(state: WorkflowState) -> str:
        count = len(state.get("protein_results", []))
        if count == 0:
            return "retry_node"
        if 1 <= count <= 10:
            return "select_node"
        return "narrow_down_node"

    def route_from_retry(state: WorkflowState) -> str:
        """After retry, route to search or end based on exhaustion."""
        retry_alternates = state.get("retry_alternates", [])
        alternate_index = state.get("retry_alternate_index", 0)
        results = state.get("protein_results", [])
        assistant_message = state.get("assistant_message")
        
        # If retry_node detected exhaustion:
        # - alternate_index >= len(alternates) (we've tried all alternates)
        # - no results found
        # - assistant_message exists (retry_node set final message)
        # Route to END if we've exhausted all alternates and have a final message
        if retry_alternates and alternate_index >= len(retry_alternates) and len(results) == 0 and assistant_message:
            print(f"[route_from_retry] Routing to END - exhaustion detected with final message")
            return END
        
        # If alternate_index=0, we haven't tried the alternate yet, so route to dynamic_search
        if retry_alternates and alternate_index == 0:
            print(f"[route_from_retry] Alternate not tried yet (index=0), routing to dynamic_search")
            return "dynamic_search"
        
        # Otherwise, search with the alternate that was just set
        print(f"[route_from_retry] Routing to dynamic_search - will try alternate")
        return "dynamic_search"

    graph.add_conditional_edges("entity_extraction", route_from_extraction)
    graph.add_conditional_edges("dynamic_search", route_from_search)
    # 🔄 CHANGED: retry_node now loops back to dynamic_search
    graph.add_conditional_edges("retry_node", route_from_retry)

    # Simple transitions
    graph.add_edge("entity_clarification", "entity_extraction")  # Loop back to extraction after clarification
    graph.add_edge("narrow_down_node", "search_clarification")
    graph.add_edge("search_clarification", "entity_extraction")  # Loop back to extraction to re-process refinement
    graph.add_edge("select_node", "protein_details_node")  # Go to details after selection
    graph.add_edge("protein_details_node", "metadata_fetcher_node")  # Fetch metadata after details
    graph.add_edge("metadata_fetcher_node", "metadata_processor_node")  # Process metadata (read from cache)
    graph.add_edge("metadata_processor_node", "pdf_downloader_node")  # Upload CSV to PDF downloader
    graph.add_edge("pdf_downloader_node", "pdf_processor_node")  # Process PDFs through GROBID
    graph.add_edge("pdf_processor_node", "methods_validator_node")  # Validate and classify methods
    graph.add_edge("methods_validator_node", END)  # End after methods validation

    # Compile with checkpointer for persistent state
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)

# ============================================================================
# HELPERS: RENDERINGS & INPUT PARSING
# ============================================================================

def _format_result_list(results: List[Any]) -> str:
    lines: List[str] = [f"Found {len(results)} entries:"]
    for i, entry in enumerate(results[:10], 1):
        line = f"{i}. {entry.accession}"
        if getattr(entry, "name", None):
            line += f" - {entry.name}"
        line += f"\n   Organism: {getattr(entry, 'organism', '')}"
        if getattr(entry, "length", None):
            line += f"\n   Length: {entry.length} aa"
        lines.append(line)
    return "\n\n".join(lines)


COMMON_ORGANISMS = {
    "human": "Homo sapiens",
    "homo sapiens": "Homo sapiens",
    "mouse": "Mus musculus",
    "mus musculus": "Mus musculus",
    "rat": "Rattus norvegicus",
    "rattus norvegicus": "Rattus norvegicus",
    "zebrafish": "Danio rerio",
    "danio rerio": "Danio rerio",
}


DEFAULT_PLAN = {
    "gene_symbols": [],
    "functional_role": None,  # NEW: receptor, ligand, enzyme, etc.
    "localizations": [],
    "go_terms": [],
    "keywords": [],
    "ptms": [],  # NEW: post-translational modifications
    "domains": [],  # NEW: protein domains
    "pathways": [],  # NEW: biological pathways
    "disease_association": None,  # NEW: disease names
    # "expression_host": None,  # NEW: E. coli, HEK293, etc.
    "length": {"min": None, "max": None},
    "mass": {"min": None, "max": None},
}

def _build_query_from_refinements(
    protein_name: str,
    organism: Optional[str],
    refinement_terms: Dict[str, Any]
) -> Tuple[str, str]:
    """
    Build UniProt query from protein_name, organism, and refinement_terms.
    Returns: (query_string, filter_summary_text)
    """
    clauses = [protein_name]
    summary_parts = []
    
    # Organism
    if organism:
        clauses.append(f'organism:"{organism}"')
        summary_parts.append(f"organism: {organism}")
    
    # Gene symbols
    for gene in refinement_terms.get("gene_symbols", []):
        clauses.append(f'gene:{gene}')
    if refinement_terms.get("gene_symbols"):
        summary_parts.append(f"genes: {', '.join(refinement_terms['gene_symbols'])}")
    
    # Functional role
    if refinement_terms.get("functional_role"):
        role = refinement_terms["functional_role"]
        clauses.append(f'keyword:"{role}"')
        summary_parts.append(f"role: {role}")
    
    # Localizations
    for loc in refinement_terms.get("localizations", []):
        clauses.append(f'cc_subcellular_location:"{loc}"')
    if refinement_terms.get("localizations"):
        summary_parts.append(f"location: {', '.join(refinement_terms['localizations'])}")
    
    # GO terms
    for go in refinement_terms.get("go_terms", []):
        clauses.append(f'go:{go}')
    if refinement_terms.get("go_terms"):
        summary_parts.append(f"GO: {', '.join(refinement_terms['go_terms'])}")
    
    # Keywords
    for kw in refinement_terms.get("keywords", []):
        clauses.append(f'keyword:"{kw}"')
    if refinement_terms.get("keywords"):
        summary_parts.append(f"keywords: {', '.join(refinement_terms['keywords'])}")
    
    # PTMs
    ptm_mapping = {
        "phosphorylation": "Phosphoprotein",
        "glycosylation": "Glycoprotein",
        "acetylation": "Acetylation",
        "ubiquitination": "Ubl conjugation",
        "methylation": "Methylation"
    }
    for ptm in refinement_terms.get("ptms", []):
        mapped = ptm_mapping.get(ptm.lower(), ptm)
        clauses.append(f'keyword:"{mapped}"')
    if refinement_terms.get("ptms"):
        summary_parts.append(f"PTMs: {', '.join(refinement_terms['ptms'])}")
    
    # Domains
    for domain in refinement_terms.get("domains", []):
        clauses.append(f'ft_domain:"{domain}"')
    if refinement_terms.get("domains"):
        summary_parts.append(f"domains: {', '.join(refinement_terms['domains'])}")
    
    # Pathways
    for pathway in refinement_terms.get("pathways", []):
        clauses.append(f'cc_pathway:"{pathway}"')
    if refinement_terms.get("pathways"):
        summary_parts.append(f"pathways: {', '.join(refinement_terms['pathways'])}")
    
    # Disease
    if refinement_terms.get("disease_association"):
        disease = refinement_terms["disease_association"]
        clauses.append(f'cc_disease:"{disease}"')
        summary_parts.append(f"disease: {disease}")
    
    # Length range (validate type)
    length_range = refinement_terms.get("length", {})
    if isinstance(length_range, dict) and (length_range.get("min") or length_range.get("max")):
        min_val = length_range.get("min") or "*"
        max_val = length_range.get("max") or "*"
        clauses.append(f'length:[{min_val} TO {max_val}]')
        summary_parts.append(f"length: {min_val}-{max_val} aa")
    
    # Mass range (validate type)
    mass_range = refinement_terms.get("mass", {})
    if isinstance(mass_range, dict) and (mass_range.get("min") or mass_range.get("max")):
        min_val = mass_range.get("min") or "*"
        max_val = mass_range.get("max") or "*"
        clauses.append(f'mass:[{min_val} TO {max_val}]')
        summary_parts.append(f"mass: {min_val}-{max_val} Da")
    
    query = " AND ".join(clauses)
    summary = "; ".join(summary_parts) if summary_parts else "No refinements"
    
    return query, summary


def _normalize_organism_name(name: str) -> str:
    if not name:
        return name
    return COMMON_ORGANISMS.get(name.lower(), name)

def _summarize_facets(results: List[Any]) -> Dict[str, Any]:
    if not results:
        return {}

    def top_items(counter: Counter, limit: int = 5) -> List[str]:
        return [f"{item} ({count})" for item, count in counter.most_common(limit)]

    organisms = Counter()
    gene_symbols = Counter()
    keywords = Counter()
    lengths: List[int] = []
    masses: List[float] = []

    # 🔧 DEBUG: Check what data we're getting from parsed results
    if results:
        # Check first 5 entries for gene/keyword data
        entries_with_genes = 0
        entries_with_keywords = 0
        for entry in results[:5]:
            gene_vals = getattr(entry, "gene_names", None)
            kw_vals = getattr(entry, "keywords", None)
            if gene_vals:
                entries_with_genes += 1
            if kw_vals:
                entries_with_keywords += 1
        print(f"[FACET DEBUG] Sample: {entries_with_genes}/5 entries have genes, {entries_with_keywords}/5 have keywords")

    for entry in results:
        if getattr(entry, "organism", None):
            organisms[entry.organism] += 1
        
        # Check if gene_names exists and is not None/empty
        gene_names = getattr(entry, "gene_names", None)
        if gene_names:
            for gene in gene_names:
                gene_symbols[gene] += 1
        
        # Check if keywords exists and is not None/empty  
        entry_keywords = getattr(entry, "keywords", None)
        if entry_keywords:
            for kw in entry_keywords:
                keywords[kw] += 1
        
        if getattr(entry, "length", None):
            lengths.append(entry.length)
        if getattr(entry, "mass", None):
            masses.append(entry.mass)

    facets = {
        "top_accessions": [getattr(r, "accession", "") for r in results[:5]],
        "top_names": [getattr(r, "name", "") for r in results[:5] if getattr(r, "name", "")],
        "organisms": top_items(organisms),
        "gene_symbols": top_items(gene_symbols),
        "keywords": top_items(keywords),
        "length_range": (min(lengths), max(lengths)) if lengths else None,
        "mass_range": (min(masses), max(masses)) if masses else None,
    }
    return facets

def _update_attempt_history(existing: List[Dict[str, Any]], attempt: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    combined = (existing or []) + [attempt]
    return combined[-limit:]

def _format_attempt_steps(attempts: List[Dict[str, Any]]) -> str:
    if not attempts:
        return "No prior attempts logged."
    lines = []
    for idx, att in enumerate(attempts[-3:], 1):
        step_descriptions = []
        for step in att.get("steps", []):
            step_descriptions.append(f"{step.get('tool')} (hits={step.get('hits')})")
        steps_text = " → ".join(step_descriptions) if step_descriptions else "no steps recorded"
        lines.append(f"{idx}. hits={att.get('result_count')} | {steps_text}")
    return "\n".join(lines)

def _build_narrow_prompt(
    normalized_query: str,
    result_count: int,
    filters_text: str,
    facets: Dict[str, Any],
    attempts: List[Dict[str, Any]],
) -> str:
    facet_lines = [
        f"Frequent gene symbols: {', '.join(facets.get('gene_symbols') or ['n/a'])}",
        f"Frequent keywords: {', '.join(facets.get('keywords') or ['n/a'])}",
        f"Length range (aa): {facets.get('length_range') or 'n/a'}",
        f"Mass range (Da): {facets.get('mass_range') or 'n/a'}",
        f"Sample accessions: {', '.join(facets.get('top_accessions') or ['n/a'])}",
    ]

    attempts_text = _format_attempt_steps(attempts)

    prompt = (
        "You are assisting a scientist to narrow down UniProt search results.\n"
        f"Current normalized query: {normalized_query or 'n/a'}\n"
        f"Result count: {result_count}\n"
        f"Applied filters: {filters_text}\n"
        "Recent search attempts:\n"
        f"{attempts_text}\n\n"
        "Facet summary:\n"
        + "\n".join(f"- {line}" for line in facet_lines)
        + "\n\n"
        "Provide a numbered list (1-3 bullets) of specific, actionable refinement suggestions or clarifying questions. "
        "Ground each suggestion in the facet data or previous attempts (cite concrete values like gene symbols, organism counts, or length ranges). "
        "If additional information is required from the user, include a direct question as one of the bullets. "
        "Keep the response concise and avoid generic advice."
    )
    prompt = (
    "You are assisting a scientist in refining a UniProt search that returned too many results. Your message should feel like a helpful interactive prompt, not an analysis.\n"
    f"Current normalized query: {normalized_query or 'n/a'}\n"
    f"Result count: {result_count}\n"
    f"Applied filters: {filters_text}\n"
    "Recent search attempts:\n"
    f"{attempts_text}\n\n"
    "Facet summary (use only these values for examples):\n"
    + "\n".join(f"- {line}" for line in facet_lines)
    + "\n\n"
    "Your task:\n"
    "- Speak directly to the user (a scientist).\n"
    "- Do NOT use meta-phrases such as 'Here are', 'based on facet data', or 'previous attempts'.\n"
    "- Do NOT explain your reasoning or mention the system, facets, or instructions.\n"
    "- Do NOT invent ranges or make arbitrary suggestions; use ONLY the provided facet values.\n"
    "- Suggestions must be realistic and meaningful to a UniProt user.\n"
    "- Tone should feel like a helpful narrowing assistant, not an analyst.\n\n"
    "Output requirements:\n"
    "- Provide ONLY a numbered list with 1–3 refinement options.\n"
    "- Each item should be a direct, user-facing suggestion.\n"
    "- Each suggestion must reference concrete facet values (e.g., gene symbols, ranges, keywords, sample accessions).\n"
    "Example stylistic pattern (DO NOT repeat this text verbatim):\n"
    "- 'You may want to focus on a specific gene symbol such as ABC or XYZ to narrow the set.'\n"
    "- 'If you're interested in shorter insulin-like peptides, you could filter to lengths closer to the lower end of the 58–2491 aa range.'\n\n"
    "Produce the final output now."
)

    return prompt

# ============================================================================
# MAIN FLOW
# ============================================================================

async def process(llm: LLMClient, mcp: ProteinSearchClient, user_message: str, thread_id: str, workflow=None):
    """Process user message through the dynamic workflow with human-in-loop nodes.
    
    State is persisted automatically via the checkpointer, so no state parameter needed.
    """
    print(f"\n{'='*80}")
    print(f"[NEW MESSAGE] {user_message}")
    
    # Use provided workflow or build new one (for backward compatibility)
    if workflow is None:
        workflow = build_workflow(llm, mcp)

    # Check for exit command - reset by using a new thread_id
    if user_message.lower() in ['exit', 'quit', 'restart', 'reset']:
        print(f"[DECISION] Restarting conversation")
        # Cleanup metadata cache for this thread before resetting
        if thread_id:
            cleanup_metadata_cache(thread_id)
        # Generate a new thread_id to clear state (checkpointer stores state per thread_id)
        new_thread_id = f"reset-{int(time.time())}"
        print(f"[STATE RESET] Using new thread_id: {new_thread_id}")
        print(f"{'='*80}\n")
        # Return empty workflow details for reset, including new conversation_id for client to use
        empty_details = {
            "protein_name": None,
            "organism": None,
            "protein_results_count": 0,
            "optimized_query": None,
            "applied_filters": {},
            "search_attempts": 0,
            "retry_count": 0,
            "new_conversation_id": new_thread_id,  # Return new thread_id for client to use
        }
        return "Conversation restarted. What protein would you like to search?", empty_details

    start_timestamp = datetime.now(timezone.utc)
    perf_start = time.perf_counter()
    usage_start_index = llm.usage_event_count
    request_start_count = llm.total_requests
    
    # Prepare config with thread_id for persistent checkpointer
    # According to LangGraph docs: checkpointer automatically loads saved state for this thread_id
    config = {"configurable": {"thread_id": thread_id}}
    
    # Check if workflow is in an interrupted state (waiting for user input)
    # Always check for interrupts, even if thread_id has "reset-" prefix
    # The "reset-" prefix only determines if we should start fresh when there's no interrupt
    interrupt_message = None
    is_resuming = False
    thread_was_reset = thread_id.startswith("reset-")
    should_start_fresh = False
    
    print(f"[DEBUG] thread_id='{thread_id}', thread_was_reset={thread_was_reset}")
    
    # Always check for pending interrupts (regardless of reset status)
    try:
        current_state = workflow.get_state(config)
        if current_state:   # State exists for this thread_id
            if current_state.tasks:
                # Check if there's a pending interrupt - workflow is waiting for user input
                for task in current_state.tasks:
                    if hasattr(task, 'interrupts') and task.interrupts:
                        is_resuming = True
                        print("[WORKFLOW] Detected pending interrupt - will resume with user input")
                        break
            else:
                # No pending tasks but state exists → workflow ended previously
                # If thread was reset, we want to start fresh
                # Otherwise, check if we should start fresh (workflow ended)
                if thread_was_reset:
                    should_start_fresh = True
                    print("[WORKFLOW] Thread was reset and no interrupt - will start fresh")
                else:
                    # Check if previous workflow ended (no tasks, but state exists)
                    # This means workflow reached END - start fresh for new conversation
                    should_start_fresh = True
                    # Cleanup metadata cache when conversation ends
                    if thread_id:
                        cleanup_metadata_cache(thread_id)
                    print("[WORKFLOW] Previous workflow ended - starting fresh conversation")
        else:
            # No saved state - fresh start
            should_start_fresh = True
            print("[WORKFLOW] No saved state - starting fresh")
    except Exception as e:
        # No saved state yet, this is a fresh conversation
        print(f"[WORKFLOW] No saved state found or error checking state: {e}")
        should_start_fresh = True
    
    if is_resuming:
        # Resume from interrupt with user's response (regardless of reset status)
        print("[WORKFLOW] Resuming workflow from interrupt...")
        result = await workflow.ainvoke(Command(resume=user_message), config=config)
    elif should_start_fresh:
        # Start fresh with complete state (workflow ended or reset)
        workflow_state = {
            "chat_history": [{"role": "user", "content": user_message}],
            "query": user_message,
            "protein_name": None,
            "organism": None,
            "protein_accession": None,
            "protein_results": [],
            "selected_protein": None,
            "refinement_terms": {},
            "optimized_query": None,
            "search_context": None,
            "applied_filters": {},
            "search_facets": None,
            "search_attempts": [],
            "filter_summary_text": None,
            "retry_count": 0,
            "retry_alternates": [],
            "retry_alternate_index": 0,
            "assistant_message": None,
            "clarification_message": None,
            "protein_metadata": None,
        }
        print("[WORKFLOW] Running fresh workflow invocation with clean state...")
        result = await workflow.ainvoke(workflow_state, config=config)
    else:
        # Normal flow: workflow in progress, partial state update
        # Build partial workflow state update with ONLY the fields we want to change
        # According to LangGraph quickstart and interrupt docs:
        # - You can pass a partial state dict when invoking - you don't need all fields
        # - checkpointer automatically loads saved state for the thread_id when you invoke
        # - Fields with reducers (like add_messages for chat_history) use the reducer to merge
        # - chat_history will auto-append the new user message to existing history via add_messages
        # - query field will update the saved query value
        # - All other fields are preserved from the saved state automatically
        workflow_state = {
            "chat_history": [{"role": "user", "content": user_message}],  # Auto-appends via add_messages reducer
            "query": user_message,  # Update query field
        }
        print("[WORKFLOW] Running fresh workflow invocation (continuing conversation)...")
        result = await workflow.ainvoke(workflow_state, config=config)
    
    # Check if new interrupt occurred
    if "__interrupt__" in result:
        print("[WORKFLOW] Interrupt detected - returning interrupt message to user")
        # Extract interrupt message from __interrupt__ list
        interrupts = result.get("__interrupt__", [])
        if interrupts and len(interrupts) > 0:
            # LangGraph interrupt is a list of Interrupt objects with 'value' attribute
            interrupt_obj = interrupts[0]
            if hasattr(interrupt_obj, 'value'):
                interrupt_message = interrupt_obj.value
            elif isinstance(interrupt_obj, dict) and 'value' in interrupt_obj:
                interrupt_message = interrupt_obj['value']
            elif isinstance(interrupt_obj, str):
                interrupt_message = interrupt_obj
    
    # Extract response from workflow result
    # State is automatically persisted by checkpointer, no need to merge back
    assistant_message = result.get("assistant_message")
    clarification_message = result.get("clarification_message")
    selected_protein = result.get("selected_protein")
    
    # Get state info for logging
    protein_name = result.get("protein_name")
    organism = result.get("organism")
    protein_results = result.get("protein_results", [])
    retry_count = result.get("retry_count", 0)
    
    print(f"[STATE UPDATE] protein={protein_name}, organism={organism}, results={len(protein_results)}, retries={retry_count}")

    decision = "fallback"
    final_message: Optional[str] = None

    # Determine response - check for messages in priority order
    # If interrupt occurred, workflow is waiting for user input
    if interrupt_message:
        decision = "await_user"
        final_message = interrupt_message
        print(f"[DECISION] Interrupt detected - awaiting user input")
    # If workflow ended (no interrupt) and has assistant_message, return final message
    elif assistant_message:
        # Check if this is a final result (protein details) or a final message (retry exhaustion)
        if selected_protein:
            decision = "details"
            final_message = assistant_message
            print(f"[DECISION] Returning protein details from workflow")
        else:
            decision = "final"
            final_message = assistant_message
            print(f"[DECISION] Final message (workflow ended): {final_message}")
    # Fallback to clarification_message if no assistant_message
    elif clarification_message:
        final_message = clarification_message
        print(f"[DECISION] Using clarification message")
    # else:
    #     # Fallback - only ask for what's actually missing
    #     missing_parts = []
    #     if not protein_name:
    #         missing_parts.append("protein name")
    #     if not organism:
    #         missing_parts.append("organism")
        
    #     if missing_parts:
    #         fallback = f"Please provide: {', '.join(missing_parts)}."
    #     else:
    #         fallback = "Please provide protein name and organism to proceed."
        
    #     final_message = fallback
    #     decision = "fallback"
    #     print(f"[DECISION] Fallback clarification")

    print(f"{'='*80}\n")
    
    # Return both the message and workflow details for API service
    # Convert metadata payload to summary for API response
    metadata_payload = result.get("protein_metadata")
    metadata_summary = None
    if metadata_payload:
        try:
            # Metadata is always stored in cache now
            if "cache_id" in metadata_payload:
                cache_id = metadata_payload.get("cache_id")
                summary = metadata_payload.get("summary", {})
                
                # Register cache_id with thread_id for cleanup
                if thread_id and cache_id:
                    register_cache_for_thread(thread_id, cache_id)
                
                metadata_summary = {
                    "total_count": summary.get("total_count", 0),
                    "columns": summary.get("columns", []),
                    "shape": summary.get("shape", [0, 0]),
                    "sources": summary.get("sources", {}),
                    "cache_id": cache_id,  # Include cache_id in response for reference
                }
            else:
                # Error: cache_id should always be present
                print("[process] Warning: cache_id not found in metadata_payload. Metadata should always be cached.")
                metadata_summary = {
                    "total_count": 0,
                    "columns": [],
                    "shape": [0, 0],
                    "sources": {},
                }
        except Exception as e:
            print(f"[process] Error creating metadata summary: {e}")
            metadata_summary = {
                "total_count": 0,
                "columns": [],
                "shape": [0, 0],
                "sources": {}
            }
    
    workflow_details = {
        "protein_name": result.get("protein_name"),
        "organism": result.get("organism"),
        "protein_results_count": len(result.get("protein_results", [])),
        "optimized_query": result.get("optimized_query"),
        "applied_filters": result.get("applied_filters", {}),
        "search_attempts": len(result.get("search_attempts", [])),
        "retry_count": result.get("retry_count", 0),
        "protein_metadata": metadata_summary,  # Summary only for API (DataFrame stays in state)
    }
    
    return final_message or "", workflow_details

# ============================================================================
# CLIENT
# ============================================================================

class ProteinClient:
    def __init__(self):
        self.mcp = ProteinSearchClient(os.getenv("MCP_SERVER_PATH"))
        self.llm = LLMClient()
        self.thread_id = "main-conversation"  # Persistent thread ID for checkpointer
        self.workflow = None  # Will be set in start()
    
    async def start(self):
        await self.mcp.start_server()
        # Build shared workflow with checkpointer
        self.workflow = build_workflow(self.llm, self.mcp)
        print("[ProteinClient] Workflow initialized with persistent checkpointer")
    
    async def stop(self):
        await self.mcp.stop_server()
    
    async def ask(self, question: str, conversation_id: Optional[str] = None) -> tuple[str, Dict[str, Any]]:
        # Use provided conversation_id or default thread_id
        thread = conversation_id if conversation_id else self.thread_id
        response, workflow_details = await process(self.llm, self.mcp, question, thread, self.workflow)
        return response, workflow_details

