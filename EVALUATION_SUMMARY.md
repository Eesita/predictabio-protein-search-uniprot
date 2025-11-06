# LangSmith Evaluation Framework Summary

## ✅ Completed Implementation

Your protein search workflow now has a complete LangSmith evaluation framework integrated. Here's what's in place:

### 1. **Automatic Tracing (Production-Ready)**
Every workflow run is automatically traced to LangSmith with:
- **Complete node-level traces**: Each of your 8 workflow nodes (entity_extraction, dynamic_search, retry_node, narrow_down_node, etc.)
- **Automatic metadata**: Timings, state transitions, decisions
- **Cost tracking**: Token usage across all LLM calls
- **Zero code changes needed**: LangGraph + LangSmith integration handles it automatically

### 2. **LLM-as-Judge Evaluator**
Two modes available:

**A) Batch Evaluation**: Using `evaluate_workflow.py`
- Uses LangSmith `aevaluate` API for proper evaluator integration
- Automatically creates feedback attached to workflow runs
- Test Cases: 6 test cases in `client/tests/eval_cases.json`

**B) Web UI Evaluation**: Endpoint `/evals`
- Judge Model: Gemini 2.0 Flash
- Path-specific criteria based on decision type
- Scores: 1-10 with detailed rationale
- Called from chat UI after user types "exit"
- Evaluates complete conversation thread WITH workflow node details
- Includes: protein_name, organism, search attempts, retries, filters, etc.
- Returns score and rationale for display in UI
- **Deep Node-Level Analysis**: For scores < 7, identifies which specific nodes failed and why
- **Actionable Feedback**: Suggests specific interventions and improvements
- **Evaluation View UI**: `http://localhost:8001/evalsview` to browse all session evaluations
- **Note**: Does NOT create LangSmith feedback (that requires batch evaluation)

### 3. **Test Cases**
Located in: `client/tests/eval_cases.json`
- 6 test cases covering:
  - Success scenarios (p53, insulin, BRCA1, EGFR)
  - Clarification scenarios (missing organism, missing protein)
  - Multiple organisms (human, mouse)

### 4. **Evaluation Scripts**

#### **Batch Evaluator (Recommended)**
`client/evaluate_workflow.py` - Use LangSmith's `aevaluate` API:
- Runs test cases in batch
- Creates LangSmith experiments automatically
- Compares workflow versions
- Attaches feedback automatically to parent workflow runs
- **This is the proper way to evaluate LangGraph workflows**

### 5. **Docker Ready**
- Updated to Python 3.12
- All dependencies included (langsmith, google-genai, etc.)
- Properly configured MCP server paths
- Currently running and healthy

## 📊 What You Get in LangSmith

For each workflow run, you'll see:

### **Main Run**
- Input: User query
- Output: Assistant response
- Metadata: Decision path, latency, retries

### **Child Traces (Individual Nodes)**
- `entity_extraction`: LLM extraction timing + tokens
- `dynamic_search`: Search query + result count
- `retry_node`: Retry attempts with alternates
- `narrow_down_node`: Refinement suggestions
- `search_clarification`: Clarification requests
- `protein_details_node`: Protein details retrieval
- etc.

### **Feedback Scores**
**Batch Evaluation only** (when running `evaluate_workflow.py`):
- `quality_score`: 1-10 from Gemini judge
- `decision_correct`: Whether the workflow took the expected path

**Web UI conversation evaluation** (after "exit"):
- Shows judge score and rationale in chat UI
- Evaluates full conversation + workflow execution details
- No LangSmith feedback attached (batch evaluation required for that)

## 🎯 How It Works

### Production: Automatic Tracing Flow
1. User sends query → `process()` function
2. LangGraph workflow runs → **Auto-traced** by LangSmith
3. Each node execution → **Individual traces** logged
4. All traces → **Visible in LangSmith UI**

### Development: Batch Evaluation Flow
1. Load test cases from `eval_cases.json`
2. Create LangSmith dataset
3. Run `aevaluate()` with evaluators
4. Results logged with experiment name and feedback

### Web UI: Conversation Evaluation Flow
1. User interacts with chat UI
2. Each query stores conversation + workflow details
3. User types "exit" to end session
4. Chat UI calls `/evals` with full conversation + workflow context
5. Gemini judge evaluates and returns score/rationale
6. Result displayed in chat UI

## 📈 Success Metrics

Based on your workflow, you should track:
- **Quality Score**: >7.0 average (from Gemini judge)
- **Success Rate**: % of runs that reach `details` decision
- **Clarification Rate**: % that correctly ask for missing info
- **Retry Rate**: % that need retry_node intervention
- **Latency**: Target <5s per query

## 🔧 Configuration

### Environment Variables (`.env`)
```bash
# LangSmith
LANGCHAIN_API_KEY=your_key
LANGCHAIN_PROJECT=protein-search-evals
LANGCHAIN_TRACING_V2=true

# Gemini Judge
GEMINI_API_KEY=your_key
GEMINI_JUDGE_MODEL=gemini-2.0-flash-exp  # optional

# Main LLM (Predicta.bio)
BASE_URL=https://ai.predicta.bio/v1
MODEL_NAME=your_model
API_KEY=your_key

# MCP Server (auto-configured in Docker)
MCP_SERVER_PATH=/app/mcp-server/build/index.js
```

## 🚀 Next Steps

1. **Deploy to Production**: Your Docker image is ready
2. **View Traces**: Go to langsmith.ai → your project
3. **Review Scores**: Check feedback tab for quality trends
4. **Iterate**: Use findings to improve prompts/nodes

## 📝 Files Modified

- ✅ `client/conversational_protein_client.py` - Core workflow with enhanced LLM-as-Judge helper
- ✅ `client/api_service.py` - Added `/evals`, `/sessions`, `/evalsview` endpoints, workflow_details tracking
- ✅ `chat_ui.html` - Integrated evaluation UI, tracks conversation + workflow details, session management
- ✅ `evalsview.html` - New evaluation view UI for browsing session traces and scores
- ✅ `client/requirements.txt` - Added langsmith>=0.2.0, google-genai, langchain-openai
- ✅ `client/tests/eval_cases.json` - Added 6 test cases
- ✅ `client/evaluate_workflow.py` - Batch evaluator with aevaluate and Gemini judge
- ✅ `Dockerfile` - Updated to Python 3.12, added evalsview.html, fixed file ordering and ownership
- ✅ `EVALUATION_SUMMARY.md` - This documentation file

## 🎓 Key Takeaways

**For Production Observability:**
- Automatic tracing is enabled via `LANGCHAIN_TRACING_V2=true`
- All nodes are traced automatically with timing and token usage
- View all traces in LangSmith UI - no additional code needed

**For Evaluation:**
- Use `evaluate_workflow.py` with LangSmith's `aevaluate` API
- Run against test cases to get quality scores and decision correctness
- Feedback is automatically attached to workflow runs
- This is the **ONLY** way to get LangSmith feedback (web UI queries show judge scores in console but don't attach feedback)

## 🚀 Quick Start

### Run Batch Evaluation
```bash
cd client
python3 evaluate_workflow.py --cases tests/eval_cases.json --project protein-search-evals
```

### Use Web UI Evaluation
1. Open chat UI: `http://localhost:8081/chat_ui.html`
2. Have a conversation about proteins
3. Type "exit" to trigger evaluation
4. View evaluation results in the chat

### Browse All Sessions
1. Open evaluation view: `http://localhost:8001/evalsview`
2. See all evaluated sessions with scores
3. Click any session to view detailed trace with node-level analysis
4. Get actionable insights on which nodes need improvement

### View Traces in LangSmith
1. Go to https://smith.langchain.com
2. Navigate to your project (default: "default")
3. Click on any run to see node-level traces

