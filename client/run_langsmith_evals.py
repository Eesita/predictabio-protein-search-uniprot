#!/usr/bin/env python3
"""
Batch LangSmith evaluation runner for the Protein Search workflow.

This script reads a JSON list of prompts, runs them through the conversational
client, and relies on the built-in LangSmith instrumentation (including the
Gemini judge) to log quality/performance/cost metrics.

Example JSON structure:
[
  {"name": "p53 human", "input": "Find p53 in human"},
  {"name": "membrane kinase", "input": "membrane kinase in mouse"}
]
"""

import argparse
import asyncio
import json
import os
from typing import Any, Dict, List

import conversational_protein_client as workflow_client
from conversational_protein_client import ProteinClient, get_langsmith_client


def load_cases(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        cases = data.get("cases") or []
    elif isinstance(data, list):
        cases = data
    else:
        raise ValueError("Evaluation file must be a list or dict with 'cases'.")
    normalized = []
    for idx, entry in enumerate(cases, 1):
        if "input" not in entry:
            raise ValueError(f"Case {idx} is missing an 'input' field.")
        normalized.append(
            {
                "name": entry.get("name") or f"case-{idx}",
                "input": entry["input"],
                "metadata": entry.get("metadata", {}),
            }
        )
    return normalized


async def run_cases(cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    client = ProteinClient()
    await client.start()

    results: List[Dict[str, Any]] = []
    try:
        for idx, case in enumerate(cases, 1):
            prompt = case["input"]
            label = case["name"]
            print(f"\n[{idx}/{len(cases)}] Running case '{label}'...")
            response = await client.ask(prompt)
            results.append(
                {
                    "name": label,
                    "input": prompt,
                    "response": response,
                }
            )
    finally:
        await client.stop()

    return results


async def main_async(args: argparse.Namespace) -> None:
    cases = load_cases(args.cases)
    if not cases:
        print("No test cases found; exiting.")
        return

    if args.project:
        os.environ["LANGSMITH_PROJECT"] = args.project
        workflow_client.LANGSMITH_PROJECT = args.project

    ls_client = get_langsmith_client()
    if ls_client:
        print(f"LangSmith project: {workflow_client.LANGSMITH_PROJECT}")
    else:
        print("LangSmith client not configured; runs will execute without remote logging.")

    results = await run_cases(cases)

    print("\nBatch complete.")
    for item in results:
        print(f"- {item['name']}: {item['response'][:120]}")

    if ls_client:
        summary = {
            "cases": [item["name"] for item in results],
            "total_cases": len(results),
        }
        try:
            run = ls_client.create_run(
                name="protein_search_batch_eval",
                project_name=workflow_client.LANGSMITH_PROJECT,
                run_type="evaluation",
                inputs={"case_count": len(results)},
                outputs={"summary": summary},
                tags=["protein-search", "batch-eval"],
            )
            run_id = getattr(run, "id", None) if run else None
            if run_id:
                ls_client.create_feedback(
                    run_id=run_id,
                    key="batch_cases",
                    value=summary,
                )
        except Exception as exc:  # pragma: no cover - network/API failure
            print(f"[LangSmith] Failed to record batch summary: {exc}")


def main() -> None:
    default_cases = os.path.join(os.path.dirname(__file__), "tests", "eval_cases.json")
    parser = argparse.ArgumentParser(description="Run LangSmith evaluations for test cases.")
    parser.add_argument(
        "--cases",
        type=str,
        default=default_cases,
        help="Path to JSON file of evaluation prompts.",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Override LangSmith project name for this batch.",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
