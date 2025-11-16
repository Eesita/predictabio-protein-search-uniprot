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
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple, Set, Annotated
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages
from langgraph.types import interrupt, Command
import pandas as pd
from protein_search_client import ProteinSearchClient
from organism_resolver import OrganismResolver
from search_agent import SearchAgent
from external_apis import fetch_metadata
from scripts.filter1 import filter_growth_factors
from scripts.extract_parameters import extract_parameters_from_dataframe
from scripts.filter_human_ecoli import filter_human_ecoli_gf
from dotenv import load_dotenv

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
# METADATA CACHE (for large datasets)
# ============================================================================

# In-memory cache for metadata that's too large for state
# Key: cache_id (e.g., "metadata_thread123_1234567890")
# Value: Full metadata payload with records
_metadata_cache: Dict[str, Dict[str, Any]] = {}

# Mapping of thread_id to cache_ids for cleanup
_thread_cache_mapping: Dict[str, List[str]] = {}

# Threshold for using external cache (number of articles)
METADATA_CACHE_THRESHOLD = 100


def get_metadata_from_cache(cache_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve full metadata from cache by cache_id"""
    return _metadata_cache.get(cache_id)


def cleanup_metadata_cache(thread_id: str):
    """Remove all cache entries for a given thread_id"""
    if thread_id in _thread_cache_mapping:
        cache_ids = _thread_cache_mapping[thread_id]
        for cache_id in cache_ids:
            if cache_id in _metadata_cache:
                del _metadata_cache[cache_id]
        del _thread_cache_mapping[thread_id]
        if cache_ids:
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

    def _history_to_str(history: List[Dict[str, str]]) -> str:
        if not history:
            return "No previous conversation"
        return "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in history)

    search_agent = SearchAgent(mcp)
    organism_resolver = OrganismResolver(mcp)

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
    - DO not include fields in the response that are not mentioned in the user's message.
    - Use chat_history to understand the user's intent and context.

    Extract any of these entities if present from the user's message:
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
        refinements = {}
        
        if json_str:
            try:
                extracted = json.loads(json_str)
                
                # Extract core entities
                if extracted.get("protein_name"):
                    protein = extracted["protein_name"].strip()
                
                if extracted.get("organism"):
                    org = _normalize_organism_name(extracted["organism"].strip())
                
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
        
        # Build summary
        summary_parts = []
        if protein:
            summary_parts.append(f"protein={protein}")
        if org:
            summary_parts.append(f"organism={org}")
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
        
        protein = state.get("protein_name") or ""
        organism = state.get("organism")
        refinement_terms = state.get("refinement_terms", {})
        
        # Resolve organism taxonomy
        if organism:
            resolved_org = await organism_resolver.resolve(organism)
            if resolved_org:
                organism = resolved_org.get("label")
                # Add taxonomy to filters for summary
                refinement_terms = dict(refinement_terms)
                refinement_terms["organism_taxonomy"] = resolved_org
        
        # Build query using simple helper
        search_query, filters_text = _build_query_from_refinements(
            protein_name=protein,
            organism=organism,
            refinement_terms=refinement_terms
        )
        
        print(f"[dynamic_search] Query: {search_query}")
        print(f"[dynamic_search] Filters: {filters_text}")
        
        # Execute search
        outcome = await search_agent.search(search_query, organism=organism, size=100)
        
        for step in outcome.steps:
            print(f"[search_agent] tool={step.tool} hits={step.hits} query='{step.query}'")
        
        results = outcome.results
        effective_query = outcome.normalized_query or search_query
        count = len(results)
        facets = _summarize_facets(results)
        
        # Record attempt
        attempt_record = {
            "query": effective_query,
            "result_count": count,
            "filters": {"protein": protein, "organism": organism, "refinements": refinement_terms},
            "steps": [step.__dict__ for step in outcome.steps],
        }
        attempts = _update_attempt_history(state.get("search_attempts", []), attempt_record)
        
        # Summary for chat
        summary_parts = [f"Search → {count} hits", f"query: {effective_query}"]
        if filters_text != "No refinements":
            summary_parts.append(f"filters: {filters_text}")
        if facets.get("organisms"):
            summary_parts.append(f"top organism: {facets['organisms'][0]}")
        summary = " | ".join(summary_parts)
        
        return {
            "protein_results": results,
            "optimized_query": effective_query,
            "applied_filters": {"protein": protein, "organism": organism, **refinement_terms},
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
        base_query = (state.get("applied_filters") or {}).get("base_query")
        gene_symbols = (state.get("applied_filters") or {}).get("gene_symbols", [])
        name_lower = getattr(selected, "name", "").lower()
        if base_query and base_query.lower() not in name_lower:
            message = (
                f"Selected protein {getattr(selected, 'accession', '')} does not appear to match the target '{base_query}'. "
                "Please choose another option or refine the query."
            )
            history = list(state.get("chat_history", []))
            history.append({"role": "assistant", "content": message})
            # Validation failed - use interrupt to ask user to select again
            user_selection = interrupt(message)
            # If user provides new selection, update query and let workflow continue
            if user_selection:
                return {
                    "query": user_selection.strip(),
                    "assistant_message": message,
                    "chat_history": history,
                }
            return {
                "assistant_message": message,
                "chat_history": history,
            }
        if gene_symbols:
            accession_genes = [g.lower() for g in getattr(selected, "gene_names", []) or []]
            if accession_genes and not any(gs.lower() in accession_genes for gs in gene_symbols):
                message = (
                    f"Selected protein {getattr(selected, 'accession', '')} lacks the expected gene symbol ({', '.join(gene_symbols)}). "
                    "Please choose another option or refine the query."
                )
                history = list(state.get("chat_history", []))
                history.append({"role": "assistant", "content": message})
                # Validation failed - use interrupt to ask user to select again
                user_selection = interrupt(message)
                # If user provides new selection, update query and let workflow continue
                if user_selection:
                    return {
                        "query": user_selection.strip(),
                        "assistant_message": message,
                        "chat_history": history,
                    }
                return {
                    "assistant_message": message,
                    "chat_history": history,
                }
        accession = getattr(selected, "accession", None) or ""
        details = selected
        if accession:
            enriched = await mcp.search_proteins(query=accession, organism=None, size=1)
            if enriched:
                details = enriched[0]
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

        protein_name = getattr(selected, "name", None) or state.get("protein_name", "")
        if not protein_name:
            print("[metadata_fetcher_node] No protein name available, skipping metadata fetch")
            return {}

        print(f"[metadata_fetcher_node] Fetching metadata for: {protein_name}")

        metadata_sources = ["pubmed", "semantic_scholar"]
        metadata_start = "2020-01"
        metadata_end = "2021-01"
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

        metadata_payload: Dict[str, Any]
        total_results = 0
        source_counts: Dict[str, int] = {}

        if isinstance(metadata_df, pd.DataFrame) and not metadata_df.empty:
            total_results = len(metadata_df)
            if 'source' in metadata_df.columns:
                source_counts = metadata_df['source'].value_counts().to_dict()
            
            # Determine if data is large enough for external cache
            use_cache = total_results > METADATA_CACHE_THRESHOLD
            
            if use_cache:
                # Large dataset - store in external cache
                # Generate unique cache_id using protein_name hash + timestamp
                protein_hash = hashlib.md5(protein_name.encode()).hexdigest()[:8]
                timestamp = int(time.time())
                cache_id = f"metadata_{protein_hash}_{timestamp}"
                
                # Store full data in cache
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
                
                # Track cache_id for cleanup (we'll need to get thread_id from config later)
                # For now, we'll use a pattern-based cleanup approach
                
                # Store only reference + summary in state
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
            else:
                # Small dataset - store directly in state
                metadata_payload = {
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
                print(f"[metadata_fetcher_node] Metadata fetched successfully: {total_results} articles (stored in state)")
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
        """Read metadata from cache (if cached) or state, convert to DataFrame, and print it."""
        print("\n[metadata_processor_node] Processing metadata...")
        
        metadata_payload = state.get("protein_metadata")
        if not metadata_payload:
            print("[metadata_processor_node] No metadata payload found in state")
            return {}
        
        metadata_df: Optional[pd.DataFrame] = None
        
        # Check if metadata is stored in cache (large dataset)
        if "cache_id" in metadata_payload:
            cache_id = metadata_payload.get("cache_id")
            print(f"[metadata_processor_node] Retrieving metadata from cache: {cache_id}")
            
            # Retrieve full data from cache
            cached_data = get_metadata_from_cache(cache_id)
            if cached_data and "records" in cached_data:
                # Convert cached records to DataFrame
                records = cached_data.get("records", [])
                columns = cached_data.get("columns", [])
                
                if records and columns:
                    metadata_df = pd.DataFrame(records, columns=columns)
                    print(f"[metadata_processor_node] Successfully loaded {len(metadata_df)} articles from cache")
                else:
                    print("[metadata_processor_node] Cache data missing records or columns")
            else:
                print(f"[metadata_processor_node] Cache entry not found or invalid for cache_id: {cache_id}")
        else:
            # Small dataset - data is already in state
            print("[metadata_processor_node] Metadata stored in state (small dataset)")
            records = metadata_payload.get("records", [])
            columns = metadata_payload.get("columns", [])
            
            if records and columns:
                metadata_df = pd.DataFrame(records, columns=columns)
                print(f"[metadata_processor_node] Successfully loaded {len(metadata_df)} articles from state")
            else:
                print("[metadata_processor_node] No records found in state metadata")
        
        # Apply filtering if DataFrame is available
        if metadata_df is not None and not metadata_df.empty:
            original_count = len(metadata_df)
            print(f"\n[metadata_processor_node] Applying growth factor filter to {original_count} articles...")
            
            # Apply filter
            filtered_df = filter_growth_factors(metadata_df)
            filtered_count = len(filtered_df) if filtered_df is not None and not filtered_df.empty else 0
            
            print(f"[metadata_processor_node] Filtering complete: {original_count} → {filtered_count} articles")
            
            # Replace original DataFrame with filtered results
            metadata_df = filtered_df
            
            # Update cache/state with filtered DataFrame
            if "cache_id" in metadata_payload:
                # Update cache with filtered data
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
            else:
                # Update state directly with filtered data
                if metadata_df is not None and not metadata_df.empty:
                    filtered_source_counts = {}
                    if 'source' in metadata_df.columns:
                        filtered_source_counts = metadata_df['source'].value_counts().to_dict()
                    
                    metadata_payload = {
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
                    print(f"[metadata_processor_node] Updated state with filtered data")
            
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
                print(f"\nFirst 5 rows:")
                print(metadata_df.head().to_string())
                
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
                        metadata_df = await extract_parameters_from_dataframe(metadata_df, start_idx=0)
                        print(f"[metadata_processor_node] Parameter extraction complete")
                        
                        # Update cache/state with extracted parameters
                        if "cache_id" in metadata_payload:
                            # Update cache with extracted data
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
                        else:
                            # Update state directly with extracted data
                            if metadata_df is not None and not metadata_df.empty:
                                filtered_source_counts = {}
                                if 'source' in metadata_df.columns:
                                    filtered_source_counts = metadata_df['source'].value_counts().to_dict()
                                
                                metadata_payload = {
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
                                print(f"[metadata_processor_node] Updated state with extracted parameters")
                    except Exception as e:
                        print(f"[metadata_processor_node] Error during parameter extraction: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"[metadata_processor_node] Parameters already extracted, skipping LLM extraction")
            
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
                        metadata_df = filter_human_ecoli_gf(metadata_df)
                        final_count = len(metadata_df) if metadata_df is not None and not metadata_df.empty else 0
                        print(f"[metadata_processor_node] Final filtering complete: {pre_final_count} → {final_count} articles")
                        
                        # Update cache/state with final filtered DataFrame
                        if "cache_id" in metadata_payload:
                            # Update cache with final filtered data
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
                        else:
                            # Update state directly with final filtered data
                            if metadata_df is not None and not metadata_df.empty:
                                final_source_counts = {}
                                if 'source' in metadata_df.columns:
                                    final_source_counts = metadata_df['source'].value_counts().to_dict()
                                
                                metadata_payload = {
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
                                print(f"[metadata_processor_node] Updated state with final filtered data")
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
                key_columns = ['title', 'abstract', 'authors', 'journal', 'doi', 'publication_year', 'Growth_Factor_Name', 'Q1_Protein_Production', 'Q3_Recombinant_Proteins', 'Q4_Species', 'Q5_Host_Organism']
                available_key_columns = [col for col in key_columns if col in metadata_df.columns]
                if available_key_columns:
                    print(f"\nSample data from key columns (including extracted parameters):")
                    print(metadata_df[available_key_columns].head(3).to_string())
                
                print(f"{'='*80}\n")
        else:
            print("[metadata_processor_node] DataFrame is empty or None after filtering")
        
        # Return updated metadata payload to update state
        return {"protein_metadata": metadata_payload}

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

    graph.add_edge(START, "entity_extraction")

    # Conditional transitions
    def route_from_extraction(state: WorkflowState) -> str:
        if state.get("protein_name") and state.get("organism"):
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
    graph.add_edge("metadata_fetcher_node", "metadata_processor_node")  # Process metadata (read from cache/state)
    graph.add_edge("metadata_processor_node", END)  # End after processing

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
        f"Top organisms: {', '.join(facets.get('organisms') or ['n/a'])}",
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
            # Check if metadata is stored in cache (large dataset)
            if "cache_id" in metadata_payload:
                # Large dataset - use summary from payload
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
                # Small dataset - data is in state
                total_count = metadata_payload.get("shape", [0, 0])[0]
                if not total_count and metadata_payload.get("records"):
                    total_count = len(metadata_payload.get("records", []))
                metadata_summary = {
                    "total_count": total_count,
                    "columns": metadata_payload.get("columns", []),
                    "shape": metadata_payload.get("shape", [total_count, len(metadata_payload.get("columns", []))]),
                    "sources": metadata_payload.get("source_counts", {}),
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

