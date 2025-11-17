from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

from protein_search_client import ProteinSearchClient, ProteinSearchResult


@dataclass
class SearchStep:
    tool: str
    query: str
    hits: int


@dataclass
class SearchOutcome:
    results: List[ProteinSearchResult]
    normalized_query: str
    steps: List[SearchStep]


class SearchAgent:
    """Heuristic router that chooses the best UniProt MCP search tool."""

    def __init__(self, client: ProteinSearchClient):
        self.client = client

    async def search(
        self,
        query: str,
        organism: Optional[str] = None,
        size: int = 100,
        plan: Optional[Dict[str, Any]] = None,
    ) -> SearchOutcome:
        query = (query or "").strip()
        if not query:
            return SearchOutcome([], "", [])

        steps: List[SearchStep] = []
        last_results: Tuple[List[ProteinSearchResult], str] = ([], query)

        results, normalized = await self.client.custom_search(query, size=size)
        normalized_query = normalized or query
        steps.append(SearchStep(tool="custom_search", query=normalized_query, hits=len(results)))
        last_results = (results, normalized_query)
        if results:
            return SearchOutcome(results, normalized_query, steps)

        return SearchOutcome(last_results[0], last_results[1], steps)
