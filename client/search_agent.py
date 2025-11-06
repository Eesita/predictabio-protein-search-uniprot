import re
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict, Any

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

    @staticmethod
    def _extract_gene(query: str) -> Optional[str]:
        gene_match = re.search(r'gene:([A-Za-z0-9\-]+)', query, re.IGNORECASE)
        if gene_match:
            return gene_match.group(1)
        return None

    @staticmethod
    def _extract_localization(query: str) -> Optional[str]:
        for pattern in [
            r'cc_subcellular_location:"([^"]+)"',
            r'location:"([^"]+)"',
            r'localization:"([^"]+)"',
        ]:
            match = re.search(pattern, query, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _extract_function_terms(query: str) -> Tuple[Optional[str], Optional[str]]:
        go_match = re.search(r'go:([A-Z]{2}:\d+)', query, re.IGNORECASE)
        go_term = go_match.group(1) if go_match else None
        func_match = re.search(r'function:"([^"]+)"', query, re.IGNORECASE)
        function_phrase = func_match.group(1) if func_match else None
        return go_term, function_phrase

    @staticmethod
    def _build_gene_query(gene: str, organism: Optional[str]) -> str:
        base = f'gene:"{gene}"'
        if organism:
            base += f' AND organism:"{organism}"'
        return base

    @staticmethod
    def _build_localization_query(localization: str, organism: Optional[str]) -> str:
        base = f'reviewed:true AND cc_subcellular_location:"{localization}"'
        if organism:
            base += f' AND organism:"{organism}"'
        return base

    @staticmethod
    def _build_function_query(
        go_term: Optional[str], function_phrase: Optional[str], organism: Optional[str]
    ) -> str:
        clauses = ["reviewed:true"]
        if go_term:
            clauses.append(f'go:"{go_term}"')
        if function_phrase:
            clauses.append(
                f'(cc_function:"{function_phrase}" OR ft_act_site:"{function_phrase}")'
            )
        if organism:
            clauses.append(f'organism:"{organism}"')
        return " AND ".join(clauses)
