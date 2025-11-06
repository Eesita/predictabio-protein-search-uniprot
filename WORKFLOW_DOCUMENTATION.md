# Conversational Protein Search Workflow Documentation

## Overview

The Conversational Protein Search workflow is a LangGraph-based stateful system that enables natural language queries for searching UniProt protein database. It uses human-in-the-loop patterns with LangGraph's `interrupt()` mechanism to pause execution and request user input when needed.

**Key Features:**
- **Entity Extraction**: Extracts protein names, organisms, and refinement terms from natural language
- **Dynamic Search**: Builds and executes UniProt queries with intelligent filtering
- **Smart Retry**: Suggests alternate protein names when searches return zero results
- **Refinement**: Provides actionable suggestions when results are too broad (>10 results)
- **Human-in-the-Loop**: Pauses execution to request missing information or user selections
- **State Persistence**: Uses LangGraph checkpointer to maintain conversation state across turns

**Technology Stack:**
- LangGraph for workflow orchestration
- LangChain for LLM integration
- UniProt MCP Server for protein data access
- Predicta.bio LLM for domain-specific understanding

---

## Architecture (Node Flow)

```
START
  ↓
entity_extraction
  ↓
  ├─→ [protein_name AND organism exist?]
  │   ├─→ YES → dynamic_search
  │   └─→ NO  → entity_clarification ──┐
  │                                     │
  │                                     ↓
  └──────────────────────────────────→ entity_extraction (loop)
  
dynamic_search
  ↓
  ├─→ [result_count == 0?]
  │   └─→ YES → retry_node
  │       ↓
  │       ├─→ [alternates exhausted?]
  │       │   ├─→ YES → END
  │       │   └─→ NO  → dynamic_search (loop)
  │
  ├─→ [1 <= result_count <= 10?]
  │   └─→ YES → select_node
  │       ↓
  │       protein_details_node
  │       ↓
  │       END
  │
  └─→ [result_count > 10?]
      └─→ YES → narrow_down_node
          ↓
          search_clarification ──┐
          ↓                       │
          entity_extraction (loop)│
```

### Routing Logic

1. **`route_from_extraction`**: Routes to `dynamic_search` if both `protein_name` and `organism` exist; otherwise to `entity_clarification`
2. **`route_from_search`**: Routes based on result count:
   - `0` → `retry_node`
   - `1-10` → `select_node`
   - `>10` → `narrow_down_node`
3. **`route_from_retry`**: Routes to `dynamic_search` if alternate not tried yet; otherwise to `END` if exhausted

---

## Nodes

### 1. entity_extraction

**Type:** LLM Node  
**Purpose:** Extracts protein name, organism, and refinement terms from user's natural language input.

**Input:**
- `query`: Current user message (string)
- `protein_name`: Previously extracted protein name (optional)
- `organism`: Previously extracted organism (optional)
- `refinement_terms`: Previously extracted refinement terms (dict)
- `chat_history`: Conversation history (list of messages)

**Output:**
- `protein_name`: Extracted protein name (if found)
- `organism`: Extracted organism name (if found)
- `refinement_terms`: Merged refinement terms dictionary
- `chat_history`: Auto-appended summary message

**Prompt:**
```
You are extracting protein search context from conversation.

USER MESSAGE: "{user_message}"

CURRENT STATE:
- protein_name: {protein_name}
- organism: {organism}
- refinements: {refinement_terms}
- chat_history: {chat_history}

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
- length: Amino acid length as {"min": <int>, "max": <int>}
- mass: Molecular mass in Daltons as {"min": <int>, "max": <int>}

Return JSON with ONLY the fields found in the message. Omit null/empty fields.
```

**Examples:**
- Input: "EGFR in nucleus" → Output: `{"protein_name": "EGFR", "refinements": {"localizations": ["nucleus"]}}`
- Input: "58aa" → Output: `{"refinements": {"length": {"min": 58, "max": 58}}}`
- Input: "human p53" → Output: `{"protein_name": "p53", "organism": "human"}`

---

### 2. entity_clarification

**Type:** Human-in-the-Loop Node (uses `interrupt()`)  
**Purpose:** Requests missing information (protein name or organism) from the user.

**Input:**
- `protein_name`: Current protein name (optional)
- `organism`: Current organism (optional)

**Output:**
- `query`: User's response (string)
- `assistant_message`: Clarification message
- `clarification_message`: Same as assistant_message
- `chat_history`: Auto-appended clarification message
- Preserves existing `protein_name`, `organism`, and `refinement_terms`

**Behavior:**
- Checks which fields are missing (`protein_name` or `organism`)
- Calls `interrupt(message)` to pause execution and wait for user input
- Updates `query` field with user's response
- Preserves existing entities to prevent data loss

**Message Format:**
```
"Please provide: {missing_fields}"
```

**Example:**
- If `protein_name` is missing: "Please provide: protein name (gene symbol, protein name, or UniProt accession)"
- If both missing: "Please provide: protein name (gene symbol, protein name, or UniProt accession), organism (e.g., Homo sapiens, Mus musculus)"

---

### 3. dynamic_search

**Type:** Tool Node (Async)  
**Purpose:** Executes UniProt search using extracted entities and refinement terms.

**Input:**
- `protein_name`: Protein name (required)
- `organism`: Organism name (optional)
- `refinement_terms`: Refinement dictionary (optional)

**Output:**
- `protein_results`: List of search results (ProteinSearchResult objects)
- `optimized_query`: Final UniProt query string
- `applied_filters`: Filters applied to the search (dict)
- `search_facets`: Facet summary (organisms, gene symbols, keywords, length/mass ranges)
- `search_attempts`: Updated attempt history
- `filter_summary_text`: Human-readable filter summary
- `chat_history`: Auto-appended search summary

**Process:**
1. Resolves organism taxonomy using `OrganismResolver`
2. Builds UniProt query using `_build_query_from_refinements()`:
   - Base: `protein_name`
   - Organism: `organism:"{organism}"`
   - Gene symbols: `gene:{symbol}`
   - Keywords: `keyword:"{keyword}"`
   - Localizations: `cc_subcellular_location:"{location}"`
   - GO terms: `go:{term}`
   - Length: `length:[{min} TO {max}]`
   - Mass: `mass:[{min} TO {max}]`
3. Executes search via `SearchAgent.search()`
4. Summarizes facets from results (top organisms, gene symbols, keywords, ranges)
5. Records attempt in `search_attempts` history

**Search Summary Format:**
```
"Search → {count} hits | query: {query} | filters: {filters} | top organism: {organism}"
```

---

### 4. retry_node

**Type:** LLM + Routing Node (Async)  
**Purpose:** Generates alternate protein names when search returns zero results.

**Input:**
- `protein_name`: Current protein name
- `organism`: Current organism
- `optimized_query`: Previous search query
- `search_attempts`: Search attempt history
- `retry_alternates`: List of previously generated alternates (optional)
- `retry_alternate_index`: Current alternate index (optional)
- `protein_results`: Current search results

**Output:**
- If generating new alternate:
  - `protein_name`: Alternate protein name
  - `retry_alternates`: List containing the alternate
  - `retry_alternate_index`: Set to 0 (not tried yet)
  - `chat_history`: Auto-appended message about trying alternate
- If exhausted:
  - `assistant_message`: Final exhaustion message
  - `chat_history`: Auto-appended exhaustion message
- If incrementing index:
  - `retry_alternate_index`: Incremented index

**Prompt:**
```
You are assisting in resolving a failed UniProt search by suggesting biologically meaningful alternate protein names.

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
{
"alternates": ["name1"]
}
```

**Logic:**
1. If `alternate_index >= len(alternates)` and `results == 0`: Return exhaustion message
2. If `alternate_index < len(alternates)`: Increment index and check exhaustion
3. If no alternates exist: Generate new alternate using LLM
4. Store alternate in `retry_alternates` list and set `retry_alternate_index = 0`

---

### 5. narrow_down_node

**Type:** LLM Node  
**Purpose:** Generates refinement suggestions when search returns >10 results.

**Input:**
- `protein_results`: Current search results (list)
- `search_attempts`: Search attempt history
- `refinement_terms`: Current refinement terms
- `search_facets`: Facet summary from results
- `filter_summary_text`: Current filter summary
- `optimized_query`: Current search query

**Output:**
- `clarification_message`: LLM-generated refinement suggestions
- `assistant_message`: Same as clarification_message
- `chat_history`: Auto-appended suggestions

**Logic:**
1. If 8+ refinement fields populated AND 2+ attempts: Show result list directly (skip LLM)
2. Otherwise: Use LLM to generate refinement suggestions based on facets

**Prompt:**
```
You are assisting a scientist to narrow down UniProt search results.
Current normalized query: {normalized_query}
Result count: {result_count}
Applied filters: {filters_text}
Recent search attempts:
{attempts_text}

Facet summary:
- Top organisms: {top_organisms}
- Frequent gene symbols: {gene_symbols}
- Frequent keywords: {keywords}
- Length range (aa): {length_range}
- Mass range (Da): {mass_range}
- Sample accessions: {top_accessions}

Provide a numbered list (1-3 bullets) of specific, actionable refinement suggestions or clarifying questions. 
Ground each suggestion in the facet data or previous attempts (cite concrete values like gene symbols, organism counts, or length ranges). 
If additional information is required from the user, include a direct question as one of the bullets. 
Keep the response concise and avoid generic advice.
```

**Example Output:**
```
1. Narrow down the length range: The current length range is quite broad (58-2491 aa). Consider refining the search to a more specific length range, such as 58-100 aa, to focus on the most relevant insulin proteins.

2. Explore gene symbols: Although there are no frequent gene symbols in the facet data, insulin is a well-studied protein with a specific gene symbol (INS). Try adding "gene_symbol:INS" to the query to see if this refines the results.

3. What type of insulin protein are you interested in? The current search results include multiple insulin proteins with different accession numbers (e.g., P01308, P06213). Are you interested in a specific type of insulin, such as human insulin or proinsulin?
```

---

### 6. search_clarification

**Type:** Human-in-the-Loop Node (uses `interrupt()`)  
**Purpose:** Requests refinement input from the user after showing suggestions.

**Input:**
- `clarification_message`: Message from `narrow_down_node` (optional)

**Output:**
- `query`: User's refinement input (string)
- `assistant_message`: Clarification message
- `clarification_message`: Same as assistant_message
- `chat_history`: Auto-appended clarification message

**Behavior:**
- Uses `clarification_message` from `narrow_down_node` if available
- Otherwise uses default: "Please refine your query: specify gene symbol, isoform, domain, or exact organism."
- Calls `interrupt(message)` to pause execution
- Updates `query` field with user's response
- Routes back to `entity_extraction` to process the refinement

---

### 7. select_node

**Type:** Human-in-the-Loop Node (uses `interrupt()`)  
**Purpose:** Presents 1-10 results to the user and waits for selection.

**Input:**
- `protein_results`: Search results (1-10 items)

**Output:**
- `selected_protein`: Selected protein object (if valid selection)

**Behavior:**
1. Formats results as numbered list:
   ```
   Please select one protein (reply with number or accession):
   1. {accession} - {name} ({organism}) | length: {length} aa
   2. ...
   ```
2. Calls `interrupt(message)` to pause execution
3. Parses user selection:
   - If number (1-10): Selects by index
   - If UniProt accession pattern: Matches by accession code
4. Returns `selected_protein` in state

**Validation:**
- If selection doesn't match expected `base_query` or `gene_symbols`, validation fails and `protein_details_node` will re-prompt

---

### 8. protein_details_node

**Type:** Tool + Validation Node (Async)  
**Purpose:** Validates selection and fetches/enriches protein details.

**Input:**
- `selected_protein`: Selected protein object
- `applied_filters`: Filters from search (contains `base_query` and `gene_symbols`)

**Output:**
- `selected_protein`: Enriched protein object
- `assistant_message`: Formatted protein details
- `chat_history`: Auto-appended details message

**Validation:**
1. Checks if `base_query` matches protein name (case-insensitive)
2. Checks if expected `gene_symbols` are present in protein's gene names
3. If validation fails: Uses `interrupt()` to ask user to select again

**Details Format:**
```
Confirmed protein details:
Accession: {accession}
Name: {name}
Organism: {organism}
Length: {length} aa
Mass: {mass} Da
Genes: {gene1}, {gene2}
```

**Enrichment:**
- If accession exists, re-queries UniProt to get full details (sequence, domains, etc.)

---

## State Schema (WorkflowState)

```python
class WorkflowState(TypedDict):
    # Conversation
    chat_history: Annotated[List[Dict[str, str]], add_messages]  # Auto-appends messages
    
    # User input
    query: Optional[str]
    
    # Extracted entities
    protein_name: Optional[str]
    organism: Optional[str]
    refinement_terms: Dict[str, Any]  # gene_symbols, localizations, length, mass, etc.
    
    # Search and results
    protein_results: List[Any]  # ProteinSearchResult objects
    selected_protein: Optional[Any]
    
    # Messaging
    clarification_message: Optional[str]
    assistant_message: Optional[str]
    
    # Dynamic query extras
    optimized_query: Optional[str]
    search_context: Optional[str]
    applied_filters: Dict[str, Any]
    search_facets: Dict[str, Any]
    search_attempts: List[Dict[str, Any]]
    filter_summary_text: Optional[str]
    
    # Retry logic
    retry_count: int
    retry_alternates: List[str]
    retry_alternate_index: int
```

---

## Key Design Patterns

### 1. Human-in-the-Loop with `interrupt()`
- Nodes call `interrupt(message)` to pause execution
- User's response becomes the return value of `interrupt()`
- Workflow resumes from the beginning of the node (node restarts)
- State is persisted via checkpointer across interruptions

### 2. State Persistence
- Uses `MemorySaver` checkpointer for in-memory state
- State is keyed by `thread_id` (conversation ID)
- Partial state updates are merged automatically
- `chat_history` uses `add_messages` reducer to auto-append

### 3. Refinement Merging
- Refinement terms are merged intelligently:
  - Lists (gene_symbols, localizations): Append unique values
  - Strings (functional_role): Overwrite
  - Dicts (length, mass): Merge with new values overriding

### 4. Query Building
- Converts structured `refinement_terms` to UniProt query syntax
- Handles organism taxonomy resolution
- Supports multiple refinement types simultaneously

---

## Example Flow

**User:** "insulin"  
**Flow:**
1. `entity_extraction` → extracts `protein_name="insulin"`, `organism=None`
2. `route_from_extraction` → routes to `entity_clarification`
3. `entity_clarification` → interrupts with "Please provide: organism"
4. **User:** "human"
5. `entity_extraction` → extracts `organism="Homo sapiens"`
6. `route_from_extraction` → routes to `dynamic_search`
7. `dynamic_search` → searches UniProt, returns 100 results
8. `route_from_search` → routes to `narrow_down_node`
9. `narrow_down_node` → generates refinement suggestions
10. `search_clarification` → interrupts with suggestions
11. **User:** "INS"
12. `entity_extraction` → extracts `refinement_terms={"gene_symbols": ["INS"]}`
13. `dynamic_search` → searches with gene symbol filter, returns 3 results
14. `route_from_search` → routes to `select_node`
15. `select_node` → interrupts with numbered list
16. **User:** "1"
17. `protein_details_node` → validates and enriches selection
18. **END** → Returns protein details

---

## Notes

- All nodes update `chat_history` with auto-append (via `add_messages` reducer)
- Human-in-the-loop nodes always route back to `entity_extraction` to re-process user input
- Retry logic generates only 1 alternate per attempt (configurable)
- State is preserved across interruptions, allowing multi-turn conversations
- Facet summarization extracts statistical insights from search results to guide refinements

