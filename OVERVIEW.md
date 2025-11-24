# Conversational Protein Search Workflow - Overview

## 1. 📄 Brief Overview

The Conversational Protein Search Workflow is designed to help users find and confirm specific protein entries from the UniProt database.

Instead of requiring users to know complex database query syntax, this workflow allows them to make requests in plain, natural language (e.g., "Find human p53 that is involved in phosphorylation").

The system is built using LangGraph as a "state machine" or "agent." It manages a complex, multi-step process that intelligently combines AI models, specialized data search tools, and Human-in-the-Loop (HITL) checkpoints to ensure the correct protein is reliably found and confirmed by the user.

---

## 2. 🎯 What It Does (Key Features)

This workflow automates the entire process of finding a protein, from a vague user idea to a confirmed data entry.

**Natural Language Understanding**: It translates a user's conversational request into structured data fields (like protein_name, organism, and refinement_terms) that our search tools can understand.

**Conversational Clarification (HITL)**: If key information is missing (e.g., the user just says "Find EGFR"), the workflow automatically pauses and asks the user for the missing details ("Which organism are you interested in?").

**Guided Filtering (HITL)**: If a search returns too many results (e.g., 500+), the workflow analyzes the results and pauses to ask the user for help, suggesting smart filters (e.g., "I found 500 results, but the top organisms are Human, Mouse, and Rat. Which one do you want?").

**Smart Retries**: If a search gets zero results, the system doesn't just fail. It uses an AI model to suggest a new search term (e.g., correcting a typo or finding a common synonym) and automatically tries again.

**User-Driven Selection (HITL)**: When a search finds a reasonable number of options (e.g., 1-10), it pauses and presents this list to the user, waiting for them to make the final selection.

**Final Data Retrieval**: Once the user confirms a protein, the workflow fetches its complete details (Accession, Name, Length, Mass, etc.) and presents the final, confirmed data.

---

## 3. 💡 Why Do We Need It?

We discovered that protein search is not a simple linear process (A -> B -> C). It's a dynamic, cyclical process that sequential scripts cannot handle.

We chose LangGraph because it allows us to build a graph with intelligent, self-revolving loops. Instead of just failing, the workflow can now automatically retry a search with a synonym (if 0 results) or loop back to the user to ask for filters (if 500+ results).

This graph structure also enables a true conversational flow. It can intelligently pause the automation for Human-in-the-Loop (HITL) checkpoints. This allows the system to ask the user for missing details ("What organism?") or present a short list of options for a final decision, turning a rigid query task into a simple, guided conversation.

---

## 4. ⚙️ How It Works (Simplified Workflow)

The system operates as a "graph," where the user's request moves from node to node. The path it takes is dynamic based on the results of each step.

Here is a text-based flowchart of the most common paths:

```
[User] -> "Find human p53"

1. Entity Extraction (AI): Parses the query.
   - protein_name: "p53"
   - organism: "human"
   (Note: If info were missing, it would pause here and ask the user.)

2. Dynamic Search (Tool): Queries UniProt with the structured data.

3. Route Results (Logic): The workflow checks the number of results.
   - If 0 results -> Go to Path A (Retry)
   - If 1-10 results -> Go to Path B (Select)
   - If >10 results -> Go to Path C (Narrow Down)
```

### Path A: Retry (0 Results)

**4a. Retry Node (AI)**: "The search for 'p53' failed. A common synonym is 'TP53'. I'll try that."

**4b. Loop**: The system automatically loops back to Step 3 (Dynamic Search) with the new term. If this also fails, it exits and tells the user.

### Path B: Select (1-10 Results)

**4a. Select Node (Human-in-Loop)**: The workflow PAUSES.

```
[Assistant] -> "I found 3 options. Please select one:
1. P04637 (Tumor suppressor p53)
2. P02340 (Cellular tumor antigen p53)
..."
[User] -> "1"
```

**4b. Go to Step 5 (Details).**

### Path C: Narrow Down (>10 Results)

**4a. Narrow Down Node (Human-in-Loop)**: The workflow PAUSES.

```
[Assistant] -> "I found 150 results. I see proteins with 'kinase' and 'receptor' functions. Which are you looking for?"
[User] -> "kinase"
```

**4b. Loop**: The system adds "kinase" as a refinement and loops back to Step 3 (Dynamic Search).

### 5. Protein Details (Tool): (Only reached from Path B)

The system fetches the full data for the user's selection (e.g., P04637).

```
[Assistant] -> "Here are the details for P04637:
Name: Tumor suppressor p53
Organism: Homo sapiens
Length: 393 aa"
```

**END**

---

## 5. 🗒️ Usage Notes & Key Concepts

**Stateful Conversations**: The workflow has memory (persisted via a checkpointer). A user can provide the protein name in one message ("Find p53") and the organism in the next ("in humans"). The system will combine them for the search.

**Human-in-the-Loop (HITL)**: This is the most important concept. The system is designed to pause and wait for user input at key decision points (marked as `interrupt` in the code). The workflow will not proceed until the user provides the clarifying information or selection.

**Observability (LangSmith)**: Every step, every AI decision, and every tool call is logged to LangSmith. This provides complete, end-to-end traceability for debugging, monitoring performance, and analyzing user interactions.

**Automated QA (LLM Judge)**: The system is configured to use an "LLM Judge" (via Gemini). This judge automatically scores the quality of the conversation, helping us monitor and improve the workflow's accuracy over time.

**Cost Structure**: A single user query can trigger multiple LLM calls: (1) for the initial extraction, (2) potentially for a retry, (3) potentially for narrowing down results, and (4) for the LLM judge. This is important to note for cost and latency monitoring.

---

## Related Documentation

- [README.md](README.md) - Project setup and quick start guide
- [DEPLOYMENT.md](DEPLOYMENT.md) - Deployment instructions

