import functools
from typing import Optional, Dict, Any

from protein_search_client import ProteinSearchClient


class OrganismResolver:
    def __init__(self, client: ProteinSearchClient):
        self.client = client
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def resolve(self, organism: Optional[str]) -> Optional[Dict[str, Any]]:
        if not organism:
            return None
        key = organism.lower().strip()
        if key in self._cache:
            return self._cache[key]

        result = await self.client.lookup_taxonomy(organism)
        if result:
            self._cache[key] = result
        return result
