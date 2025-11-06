#!/usr/bin/env python3
"""
LangSmith evaluation runner for Protein Search workflow using aevaluate API.

Based on: https://docs.langchain.com/langsmith/evaluate-graph#create-an-evaluator

This script uses LangSmith's aevaluate to run the workflow against test cases
and evaluate them with LLM-as-judge metrics.
"""

import asyncio
import json
import os
import sys
from typing import Any, Dict, Optional

# Add client directory to path
sys.path.insert(0, os.path.dirname(__file__))

from conversational_protein_client import (
    ProteinClient, 
    State,
    process,
    LLMClient,
    ProteinSearchClient,
)
from dotenv import load_dotenv

load_dotenv()

try:
    from langsmith import aevaluate, Client
    from google import genai as google_genai
    LANGCHAIN_AVAILABLE = True
except ImportError:
    print("ERROR: langsmith>=0.2.0 not installed. Install with: pip install --upgrade langsmith google-genai")
    sys.exit(1)

LANGCHAIN_API_KEY = os.getenv("LANGCHAIN_API_KEY")
if not LANGCHAIN_API_KEY:
    print("ERROR: LANGCHAIN_API_KEY not set in environment")
    sys.exit(1)

# Initialize Gemini judge
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("ERROR: GEMINI_API_KEY not set in environment")
    sys.exit(1)

judge_client = google_genai.Client(api_key=GEMINI_API_KEY)


async def workflow_wrapper(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrapper to run workflow and return structured output for aevaluate.
    
    Args:
        inputs: dict with 'question' key
        
    Returns:
        dict with 'response' and workflow metadata
    """
    # Initialize fresh state for each run
    mcp = ProteinSearchClient(os.getenv("MCP_SERVER_PATH"))
    llm = LLMClient()
    state = State()
    
    try:
        await mcp.start_server()
        
        user_message = inputs.get("question", "")
        response, final_state = await process(llm, mcp, user_message, state)
        
        # Determine decision path
        decision = "fallback"
        if final_state.await_user and final_state.assistant_message:
            decision = "await_user"
        elif final_state.selected_protein and final_state.assistant_message:
            decision = "details"
        
        # Return output for evaluation
        return {
            "response": response,
            "decision": decision,
            "protein_name": final_state.protein_name,
            "organism": final_state.organism,
            "result_count": len(final_state.protein_results),
            "retry_count": final_state.retry_count,
        }
    finally:
        await mcp.stop_server()


async def quality_judge(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> bool:
    """
    LLM-as-judge evaluator following LangSmith pattern.
    Returns True if response is correct, False otherwise.
    """
    instructions = (
        "Given an actual answer and an expected answer, determine whether"
        " the actual answer contains all of the information in the"
        " expected answer. Respond with 'CORRECT' if the actual answer"
        " does contain all of the expected information and 'INCORRECT'"
        " otherwise. Do not include anything else in your response."
    )
    
    actual_answer = outputs.get("response", "")
    expected = reference_outputs.get("expected", {})
    expected_answer = expected.get("description", "")
    
    user_msg = (
        f"{instructions}\n\n"
        f"ACTUAL ANSWER: {actual_answer}"
        f"\n\nEXPECTED ANSWER: {expected_answer}"
    )
    
    def _call_judge():
        try:
            response = judge_client.models.generate_content(
                model="gemini-2.0-flash-exp",
                contents=user_msg,
            )
            return response.text if response else None
        except Exception as e:
            print(f"[Judge] Error: {e}")
            return None
    
    result = await asyncio.to_thread(_call_judge)
    if result:
        # Clean up result and check for "CORRECT"
        clean_result = result.strip().upper()
        return "CORRECT" in clean_result
    return False


def decision_correct(outputs: Dict[str, Any], reference_outputs: Dict[str, Any]) -> bool:
    """
    Check if workflow took the correct decision path.
    
    Args:
        outputs: actual workflow outputs
        reference_outputs: expected outputs from test case
        
    Returns:
        bool: True if decision matches expected
    """
    expected = reference_outputs.get("expected", {})
    expected_decision = expected.get("decision")
    actual_decision = outputs.get("decision")
    
    return expected_decision == actual_decision


async def load_test_cases(cases_path: str) -> list:
    """Load and format test cases from JSON file."""
    with open(cases_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    if isinstance(data, dict):
        cases = data.get("cases", [])
    elif isinstance(data, list):
        cases = data
    else:
        raise ValueError("Cases file must be a list or dict with 'cases' key")
    
    # Format for aevaluate
    formatted = []
    for case in cases:
        formatted.append({
            "question": case["input"],
            "expected": case.get("expected_output", {}),
        })
    
    return formatted


async def main():
    """Run evaluation using LangSmith aevaluate."""
    cases_path = os.path.join(os.path.dirname(__file__), "tests", "eval_cases.json")
    
    print("Loading test cases...")
    test_cases = await load_test_cases(cases_path)
    print(f"Loaded {len(test_cases)} test cases")
    
    print("\nCreating LangSmith dataset...")
    ls_client = Client()
    dataset = ls_client.create_dataset(
        "protein-search-workflow",
        description="Evaluation dataset for protein search workflow",
    )
    
    for case in test_cases:
        ls_client.create_example(
            dataset_id=dataset.id,
            inputs={"question": case["question"]},
            outputs={"expected": case["expected"]},
        )
    
    print(f"Created dataset: {dataset.id}")
    
    print("\nRunning evaluations...")
    try:
        results = await aevaluate(
            workflow_wrapper,
            data=dataset.id,
            evaluators=[quality_judge, decision_correct],
            max_concurrency=2,
            experiment_prefix="protein-search-baseline",
        )
        
        print(f"\n✅ Evaluation complete!")
        print(f"Results: {results}")
        
    except Exception as e:
        print(f"\n❌ Evaluation failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

