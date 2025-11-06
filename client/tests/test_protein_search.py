import json
import os
import sys
from typing import Any, Dict, Optional, Tuple
from unittest import IsolatedAsyncioTestCase

CURRENT_DIR = os.path.dirname(__file__)
CLIENT_DIR = os.path.dirname(CURRENT_DIR)
if CLIENT_DIR not in sys.path:
    sys.path.insert(0, CLIENT_DIR)

from protein_search_client import ProteinSearchClient  # noqa: E402
from search_agent import SearchAgent, SearchOutcome  # noqa: E402
from protein_search_client import ProteinSearchResult  # noqa: E402


class TestProteinSearchClientCustomSearch(IsolatedAsyncioTestCase):
    async def test_custom_search_parses_normalized_query(self):
        client = ProteinSearchClient(server_path="fake")

        captured: Dict[str, Any] = {}

        async def fake_call_tool(_self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
            captured["tool_name"] = tool_name
            captured["params"] = params
            expected = {
                "query": 'gene:TP53 AND organism_id:"9606"',
                "originalQuery": params["query"],
                "results": [
                    {
                        "primaryAccession": "P04637",
                        "proteinDescription": {
                            "recommendedName": {
                                "fullName": {"value": "Cellular tumor antigen p53"}
                            }
                        },
                        "organism": {"scientificName": "Homo sapiens"},
                        "sequence": {"length": 393, "molWeight": 43608},
                    }
                ],
            }
            return {"content": [{"text": json.dumps(expected)}]}

        client._call_tool = fake_call_tool.__get__(client, ProteinSearchClient)  # type: ignore

        results, normalized_query = await client.custom_search("gene:TP53 AND organism:\"human\"")
        self.assertEqual(captured["tool_name"], "custom_search")
        self.assertEqual(normalized_query, 'gene:TP53 AND organism_id:"9606"')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].accession, "P04637")
        self.assertEqual(results[0].name, "Cellular tumor antigen p53")
        self.assertEqual(results[0].organism, "Homo sapiens")
        self.assertEqual(results[0].length, 393)

    async def test_custom_search_handles_non_json_response(self):
        client = ProteinSearchClient(server_path="fake")

        async def fake_call_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
            return {"content": [{"text": "not-json"}]}

        client._call_tool = fake_call_tool.__get__(client, ProteinSearchClient)  # type: ignore

        results, normalized_query = await client.custom_search("gene:TP53")

        self.assertEqual(normalized_query, "gene:TP53")
        self.assertEqual(results, [])


class StubProteinSearchClient:
    def __init__(self):
        self.calls = []

    async def search_by_gene(self, gene: str, organism: Optional[str] = None, size: int = 100):
        self.calls.append(("gene", gene, organism, size))
        return [
            ProteinSearchResult(
                accession="P04637",
                name="Cellular tumor antigen p53",
                organism=organism or "Homo sapiens",
                length=393,
                mass=None,
            )
        ]

    async def search_by_localization(
        self, localization: str, organism: Optional[str] = None, size: int = 100
    ):
        self.calls.append(("localization", localization, organism, size))
        return [
            ProteinSearchResult(
                accession="Q9Y6K9",
                name="Protein X",
                organism=organism or "Homo sapiens",
            )
        ]

    async def search_by_function(
        self,
        function: Optional[str] = None,
        go_term: Optional[str] = None,
        organism: Optional[str] = None,
        size: int = 100,
    ):
        self.calls.append(("function", function, go_term, organism, size))
        return [
            ProteinSearchResult(
                accession="Q12345",
                name="Functional protein",
                organism=organism or "Homo sapiens",
            )
        ]

    async def custom_search(self, query: str, size: int = 100, format: str = "json"):
        self.calls.append(("custom", query, size))
        return (
            [
                ProteinSearchResult(
                    accession="P99999",
                    name="Fallback protein",
                    organism="Homo sapiens",
                )
            ],
            query + " normalized",
        )

    async def lookup_taxonomy(self, query: str):
        return {"id": 9606, "label": "Homo sapiens"}


class TestSearchAgent(IsolatedAsyncioTestCase):
    async def test_search_agent_prefers_gene_tool(self):
        stub = StubProteinSearchClient()
        agent = SearchAgent(stub)  # type: ignore[arg-type]

        outcome = await agent.search("gene:TP53", organism="human")

        self.assertEqual(outcome.steps[0].tool, "custom_search")
        self.assertEqual(outcome.results[0].accession, "P99999")

    async def test_search_agent_uses_localization_tool(self):
        stub = StubProteinSearchClient()
        agent = SearchAgent(stub)  # type: ignore[arg-type]

        outcome = await agent.search('cc_subcellular_location:"nucleus"', organism="mouse")

        self.assertEqual(outcome.steps[0].tool, "custom_search")
        self.assertEqual(outcome.results[0].accession, "P99999")

    async def test_search_agent_falls_back_to_custom(self):
        stub = StubProteinSearchClient()
        agent = SearchAgent(stub)  # type: ignore[arg-type]

        outcome = await agent.search("some free text search")

        self.assertEqual(outcome.results[0].accession, "P99999")
        self.assertEqual(outcome.normalized_query, "some free text search normalized")
        self.assertEqual(stub.calls[-1][0], "custom")
        self.assertEqual(outcome.steps[-1].tool, "custom_search")
