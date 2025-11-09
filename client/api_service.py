#!/usr/bin/env python3
"""
FastAPI microservice for conversational protein search
"""

import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from conversational_protein_client import ProteinClient

# Global client instance
conversational_client: Optional[ProteinClient] = None

# In-memory session storage (in production, use a database)
sessions_store: Dict[str, Dict[str, Any]] = {}
session_traces: Dict[str, List[Dict[str, Any]]] = {}

app = FastAPI(title="Conversational Protein Search API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class QueryRequest(BaseModel):
    query: str
    conversation_id: Optional[str] = None

class QueryResponse(BaseModel):
    response: str
    conversation_id: Optional[str] = None
    workflow_details: Optional[Dict[str, Any]] = None

@app.on_event("startup")
async def startup():
    """Initialize the conversational client on startup."""
    global conversational_client
    conversational_client = ProteinClient()
    await conversational_client.start()
    print("Conversational Protein Search API started")

@app.on_event("shutdown")
async def shutdown():
    """Clean up on shutdown."""
    global conversational_client
    if conversational_client:
        await conversational_client.stop()
    print("Conversational Protein Search API stopped")

@app.get("/health")
async def health_check() -> Dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "conversational-protein-search",
        "mcp_server": "running" if conversational_client else "not_started"
    }

@app.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest) -> QueryResponse:
    """
    Process a conversational protein search query.
    
    Args:
        request: Query request with question and optional conversation ID
        
    Returns:
        Response with formatted protein data
    """
    if not conversational_client:
        raise HTTPException(status_code=503, detail="Service not available")
    
    try:
        # Pass conversation_id to ask() so it's used as thread_id
        response, workflow_details = await conversational_client.ask(request.query, conversation_id=request.conversation_id)
        
        # Use conversation_id from workflow_details (or new_conversation_id for exit command)
        conversation_id = workflow_details.get("new_conversation_id") or workflow_details.get("conversation_id") or request.conversation_id
        
        return QueryResponse(
            response=response,
            conversation_id=conversation_id,
            workflow_details=workflow_details
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing query: {str(e)}")

class EvalRequest(BaseModel):
    question: str
    answer: str
    decision: Optional[str] = "details"
    session_id: Optional[str] = None
    workflow_details: Optional[List[Dict[str, Any]]] = None

class EvalResponse(BaseModel):
    success: bool
    score: Optional[float] = None
    rationale: Optional[str] = None
    error: Optional[str] = None

@app.post("/evals", response_model=EvalResponse)
async def create_evaluation(request: EvalRequest) -> EvalResponse:
    """
    Evaluate a question-answer pair using Gemini LLM-as-Judge.
    
    This endpoint runs an LLM-as-Judge evaluation and returns the score
    and rationale for display in the UI. It also stores the session data.
    """
    try:
        from conversational_protein_client import run_llm_judge
    except ImportError as e:
        return EvalResponse(success=False, error=f"Import error: {e}")
    
    # Run the LLM judge
    judge_context = {"decision": request.decision}
    judge_result = await run_llm_judge(
        user_message=request.question,
        assistant_message=request.answer,
        context=judge_context
    )
    
    if not judge_result:
        return EvalResponse(success=False, error="Judge evaluation failed")
    
    # Store session data if session_id provided
    if request.session_id:
        session_id = request.session_id
        timestamp = datetime.now().isoformat()
        
        # Build trace nodes from workflow details
        trace_nodes = []
        if request.workflow_details:
            for i, details in enumerate(request.workflow_details):
                # Determine node type based on workflow_details
                node_name = "unknown"
                if details.get("retry_count", 0) > 0:
                    node_name = "retry_node"
                elif details.get("await_user"):
                    node_name = "clarification_node"
                elif details.get("protein_results_count", 0) > 0:
                    node_name = "search_results_node"
                else:
                    node_name = f"workflow_node_{i+1}"
                
                status = "success"
                if details.get("retry_count", 0) > 0:
                    status = "fallback"
                elif details.get("await_user"):
                    status = "await_user"
                
                trace_nodes.append({
                    "name": node_name,
                    "input": request.question if i == 0 else {},
                    "output": details,
                    "status": status
                })
        
        # Store trace data
        session_traces[session_id] = trace_nodes
        sessions_store[session_id] = {
            "timestamp": timestamp,
            "message_count": len(request.workflow_details) if request.workflow_details else 1,
            "overall_score": judge_result.get("score"),
            "evaluation": {
                "score": judge_result.get("score"),
                "rationale": judge_result.get("rationale")
            }
        }
    
    return EvalResponse(
        success=True,
        score=judge_result.get("score"),
        rationale=judge_result.get("rationale")
    )

@app.get("/sessions")
async def list_sessions() -> List[Dict[str, Any]]:
    """List all evaluation sessions."""
    result = []
    for session_id, session_data in sessions_store.items():
        result.append({
            "id": session_id,
            "timestamp": session_data.get("timestamp"),
            "message_count": session_data.get("message_count", 0),
            "score": session_data.get("overall_score")
        })
    # Sort by timestamp descending
    result.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return result

@app.get("/sessions/{session_id}/trace")
async def get_session_trace(session_id: str) -> Dict[str, Any]:
    """Get detailed trace for a specific session."""
    if session_id not in session_traces:
        raise HTTPException(status_code=404, detail="Session not found")
    
    trace_data = {
        "session_id": session_id,
        "nodes": session_traces.get(session_id, []),
        "overall_evaluation": sessions_store.get(session_id, {}).get("evaluation")
    }
    return trace_data

@app.get("/")
async def serve_chat_ui():
    """Serve the main chat UI."""
    # Files are in /app/ directory (same as api_service.py)
    html_path = os.path.join("/app", "chat_ui.html")
    return FileResponse(html_path)

@app.get("/evalsview")
async def serve_evals_view():
    """Serve the evaluation view HTML."""
    html_path = os.path.join("/app", "evalsview.html")
    return FileResponse(html_path)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)
