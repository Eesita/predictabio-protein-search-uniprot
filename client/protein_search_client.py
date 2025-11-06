#!/usr/bin/env python3
"""Protein search MCP client used by the FastAPI service."""

import json
import os
import subprocess
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass


@dataclass
class ProteinSearchResult:
    """Represents a protein search result."""
    accession: str
    name: str
    organism: str
    description: Optional[str] = None
    gene_names: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    length: Optional[int] = None
    mass: Optional[float] = None


class ProteinSearchClient:
    """
    Client focused on protein search functionality using the UniProt MCP server.
    """
    
    def __init__(self, server_path: Optional[str] = None):
        """
        Initialize the protein search client.
        
        Args:
            server_path: Path to the UniProt MCP server executable
        """
        self.server_path = server_path or os.getenv(
            "MCP_SERVER_PATH",
            "/app/mcp-server/build/index.js",
        )
        self.process: Optional[subprocess.Popen] = None
        self.request_id = 0
        
    async def start_server(self) -> None:
        """Start the MCP server process."""
        try:
            self.process = subprocess.Popen(
                ["node", self.server_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            print("UniProt MCP server started successfully")
            
            # ✅ ADD THIS: Forward MCP server's stderr to Docker logs
            import threading
        
            def log_stderr():
                """Read and print stderr from MCP server"""
                if self.process and self.process.stderr:
                    for line in self.process.stderr:
                        print(f"[MCP] {line.rstrip()}", flush=True)
        
            # Start stderr logging in background thread
            stderr_thread = threading.Thread(target=log_stderr, daemon=True)
            stderr_thread.start()

            # Initialize MCP connection
            await self._initialize_mcp()
            
        except Exception as e:
            raise RuntimeError(f"Failed to start MCP server: {e}")
    
    async def stop_server(self) -> None:
        """Stop the MCP server process."""
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None
            print("UniProt MCP server stopped")
    
    def _get_next_request_id(self) -> int:
        """Get the next request ID for MCP communication."""
        self.request_id += 1
        return self.request_id
    
    async def _initialize_mcp(self) -> None:
        """Initialize MCP connection."""
        init_request = {
            "jsonrpc": "2.0",
            "id": self._get_next_request_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {}
                },
                "clientInfo": {
                    "name": "protein-search-client",
                    "version": "1.0.0"
                }
            }
        }
        
        await self._send_request(init_request)
        print("MCP connection initialized")
    
    async def _send_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a request to the MCP server and return the response.
        
        Args:
            request: The JSON-RPC request
            
        Returns:
            The response from the server
        """
        if not self.process:
            raise RuntimeError("MCP server not started. Call start_server() first.")
        
        try:
            # Send request
            request_str = json.dumps(request) + "\n"

            # # ✅ ADD THIS: Log the serialized JSON
            # print(f"[ENCODING DEBUG] JSON-RPC request string: {request_str[:500]!r}")
            # print(f"[ENCODING DEBUG] Request bytes sample: {request_str.encode('utf-8')[:200]}")

            self.process.stdin.write(request_str)
            self.process.stdin.flush()
            
            # Read response
            response_line = self.process.stdout.readline()
            if not response_line:
                raise RuntimeError("No response from server")

            # # ✅ ADD THIS: Log what we received
            # print(f"[ENCODING DEBUG] Response line: {response_line[:500]!r}")
            
            response = json.loads(response_line.strip())
            
            if "error" in response:
                raise RuntimeError(f"Server error: {response['error']}")
            
            return response.get("result", {})
            
        except Exception as e:
            raise RuntimeError(f"Failed to communicate with MCP server: {e}")
    
    async def _call_tool(self, tool_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Call an MCP tool."""
        request = {
            "jsonrpc": "2.0",
            "id": self._get_next_request_id(),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": parameters
            }
        }
        
        return await self._send_request(request)
    
    def _parse_protein_results(self, response: Dict[str, Any]) -> List[ProteinSearchResult]:
        """Parse protein results from MCP response."""
        results = []
        
        # Handle different response formats
        if isinstance(response, dict):
            if "results" in response:
                data = response["results"]
            elif "data" in response:
                data = response["data"]
            elif "content" in response:
                # Handle MCP content format
                content = response["content"]
                if isinstance(content, list) and len(content) > 0:
                    # Extract text content and parse as JSON
                    text_content = content[0].get("text", "")
                    try:
                        parsed_data = json.loads(text_content)
                        if isinstance(parsed_data, list):
                            data = parsed_data
                        elif isinstance(parsed_data, dict) and "results" in parsed_data:
                            data = parsed_data["results"]
                        else:
                            data = [parsed_data]
                    except json.JSONDecodeError:
                        data = []
                else:
                    data = []
            else:
                data = [response]
        elif isinstance(response, list):
            data = response
        else:
            data = []

        if isinstance(data, dict):
            if "results" in data and isinstance(data["results"], list):
                data = data["results"]
            else:
                data = [data]

        # 🔧 DEBUG: Check what fields are in the raw API response (first item only)
        if data and isinstance(data, list) and len(data) > 0:
            first_item = data[0]
            if isinstance(first_item, dict):
                has_genes = "genes" in first_item or "gene" in first_item
                has_keywords = "keywords" in first_item
                print(f"[PARSE DEBUG] API response has 'genes': {has_genes}, 'keywords': {has_keywords}")
        
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                # Extract basic info from different possible field names
                accession = item.get("accession", item.get("primaryAccession", ""))
                
                # Extract protein name from UniProt's nested structure
                name = ""
                protein_desc = item.get("proteinDescription")
                if isinstance(protein_desc, dict):
                    recommended_name = protein_desc.get("recommendedName")
                    if isinstance(recommended_name, dict):
                        full_name = recommended_name.get("fullName")
                        if isinstance(full_name, dict) and "value" in full_name:
                            name = full_name["value"]
                        elif isinstance(full_name, str):
                            name = full_name
                
                # Fallback to old field names if the nested structure is not found
                if not name:
                    name = item.get("proteinName", item.get("name", ""))
                
                # Handle organism info - extract scientific name from complex structure
                organism = ""
                organism_info = item.get("organism", item.get("organismName", ""))
                if isinstance(organism_info, dict):
                    organism = organism_info.get("scientificName", "")
                elif isinstance(organism_info, str):
                    organism = organism_info
                
                description = item.get("description", "")
                
                # Extract gene names - API uses "genes" (plural) not "gene"
                gene_names = []
                # Try both "genes" (new format) and "gene" (old format) for compatibility
                gene_field = item.get("genes") or item.get("gene")
                if gene_field:
                    if isinstance(gene_field, list):
                        for gene in gene_field:
                            if isinstance(gene, dict):
                                # Try different possible structures
                                if "geneName" in gene:
                                    gene_name_obj = gene["geneName"]
                                    if isinstance(gene_name_obj, dict):
                                        # Extract value from geneName object
                                        gene_name = gene_name_obj.get("value") or gene_name_obj.get("name")
                                        if gene_name:
                                            gene_names.append(gene_name)
                                elif "value" in gene:
                                    gene_names.append(gene["value"])
                                elif "name" in gene:
                                    gene_names.append(gene["name"])
                    elif isinstance(gene_field, dict):
                        # Single gene object
                        if "geneName" in gene_field:
                            gene_name_obj = gene_field["geneName"]
                            if isinstance(gene_name_obj, dict):
                                gene_name = gene_name_obj.get("value") or gene_name_obj.get("name")
                                if gene_name:
                                    gene_names.append(gene_name)
                        elif "value" in gene_field:
                            gene_names.append(gene_field["value"])
                        elif "name" in gene_field:
                            gene_names.append(gene_field["name"])
                
                # Extract keywords - API uses "name" not "value"
                keywords = []
                if "keywords" in item:
                    keyword_info = item["keywords"]
                    if isinstance(keyword_info, list):
                        for kw in keyword_info:
                            if isinstance(kw, dict):
                                # Try "name" first (current API format), then "value" for compatibility
                                keyword = kw.get("name") or kw.get("value")
                                if keyword:
                                    keywords.append(keyword)
                
                # 🔧 DEBUG: Log parsing summary for first entry
                if idx == 0:
                    print(f"[PARSE DEBUG] First entry: {len(gene_names)} gene(s), {len(keywords)} keyword(s)")
                
                # Extract length and mass from sequence info
                length = None
                mass = None
                if "sequence" in item:
                    seq_info = item["sequence"]
                    if isinstance(seq_info, dict):
                        length = seq_info.get("length")
                        mass = seq_info.get("molWeight")
                
                result = ProteinSearchResult(
                    accession=accession,
                    name=name,
                    organism=organism,
                    description=description,
                    gene_names=gene_names,
                    keywords=keywords,
                    length=length,
                    mass=mass
                )
                results.append(result)
        
        return results

    async def lookup_taxonomy(self, query: str) -> Optional[Dict[str, Any]]:
        try:
            params = {
                "query": query,
            }
            response = await self._call_tool("lookup_taxonomy", params)
            data = response.get("result") or response.get("results") or response
            if isinstance(response, dict) and "content" in response:
                content = response["content"]
                if isinstance(content, list) and content:
                    text = content[0].get("text", "")
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict) and "result" in parsed:
                            data = parsed["result"]
                        else:
                            data = parsed
                    except json.JSONDecodeError:
                        return None

            if isinstance(data, dict) and "id" in data and "label" in data:
                return {"id": data["id"], "label": data["label"]}

            entries = []
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict) and "results" in data and isinstance(data["results"], list):
                entries = data["results"]

            if not entries:
                return None

            entry = entries[0]
            organism_info = entry.get("organism") or {}
            taxon_id = (
                organism_info.get("taxonId")
                or entry.get("taxonId")
                or entry.get("taxonomyId")
                or entry.get("taxId")
            )
            label = (
                organism_info.get("scientificName")
                or entry.get("scientificName")
                or entry.get("taxonomyName")
                or entry.get("commonName")
            )
            if taxon_id and label:
                return {"id": taxon_id, "label": label}
            return None
        except Exception as e:
            print(f"Error looking up taxonomy: {e}")
            return None
    
    async def search_proteins(self, query: str, organism: Optional[str] = None, 
                            size: int = 100, format: str = "json") -> List[ProteinSearchResult]:
        """
        Search for proteins in UniProt database.
        
        Args:
            query: Search query (protein name, keyword, or complex search)
            organism: Organism name or taxonomy ID to filter results
            size: Number of results to return (1-500, default: 100)
            format: Output format (json, tsv, fasta, xml)
            
        Returns:
            List of ProteinSearchResult objects
        """
        try:
            params = {
                "query": query,
                "size": size,
                "format": format
            }
            if organism:
                params["organism"] = organism
                
            response = await self._call_tool("search_proteins", params)
            return self._parse_protein_results(response)
            
        except Exception as e:
            print(f"Error searching proteins: {e}")
            return []

    async def custom_search(self, query: str, size: int = 100, format: str = "json") -> Tuple[List[ProteinSearchResult], Optional[str]]:
        """
        Execute an advanced UniProt search while normalizing organism names to taxonomy IDs.

        Args:
            query: Advanced UniProt search query possibly containing organism filters
            size: Number of results to return (1-1000, default: 100)
            format: Output format (json, tsv, fasta, xml)

        Returns:
            Tuple of (ProteinSearchResult list, normalized query string if available)
        """
        try:
            # # ✅ ADD THIS: Log what we're sending
            # print(f"[ENCODING DEBUG] Query to MCP: {query!r}")
            # print(f"[ENCODING DEBUG] Query bytes: {query.encode('utf-8')}")

            params: Dict[str, Any] = {
                "query": query,
                "size": size,
                "format": format,
            }

            # # ✅ ADD THIS: Log the JSON-RPC request
            # print(f"[ENCODING DEBUG] Params dict: {params}")

            response = await self._call_tool("custom_search", params)

            # # ✅ ADD THIS: Debug logging
            # print(f"[DEBUG] Raw MCP response type: {type(response)}")
            # print(f"[DEBUG] Raw MCP response keys: {response.keys() if isinstance(response, dict) else 'not a dict'}")
            # if isinstance(response, dict) and "content" in response:
            #     print(f"[DEBUG] Content length: {len(response['content'])}")
            #     if response["content"]:
            #         text_preview = response["content"][0].get("text", "")[:200]
            #         print(f"[DEBUG] Text preview: {text_preview}")

            normalized_query: Optional[str] = None
            if isinstance(response, dict) and "content" in response:
                content = response["content"]
                if isinstance(content, list) and content:
                    text_content = content[0].get("text", "")
                    try:
                        parsed = json.loads(text_content)
                        if isinstance(parsed, dict):
                            normalized_query = parsed.get("query")
                            # Handle nested results structure from MCP server
                            if "results" in parsed:
                                results_data = parsed["results"]
                                # UniProt API returns {"results": [...]}
                                # But we wrap it as {"results": {"results": [...]}}
                                if isinstance(results_data, dict) and "results" in results_data:
                                    response = {"results": results_data["results"]}
                                else:
                                    response = {"results": results_data}
                    except json.JSONDecodeError:
                        pass

            results = self._parse_protein_results(response)
            return results, normalized_query or query

        except Exception as e:
            print(f"Error performing custom search: {e}")
            return [], None

    async def search_by_gene(self, gene: str, organism: Optional[str] = None, 
                           size: int = 100) -> List[ProteinSearchResult]:
        """
        Search for proteins by gene name or symbol.
        
        Args:
            gene: Gene name or symbol (e.g., BRCA1, INS)
            organism: Organism name or taxonomy ID to filter results
            size: Number of results to return (1-500, default: 100)
            
        Returns:
            List of ProteinSearchResult objects
        """
        try:
            params = {
                "gene": gene,
                "size": size
            }
            if organism:
                params["organism"] = organism
                
            response = await self._call_tool("search_by_gene", params)
            return self._parse_protein_results(response)
            
        except Exception as e:
            print(f"Error searching by gene: {e}")
            return []

    async def search_by_function(
        self,
        function: Optional[str] = None,
        go_term: Optional[str] = None,
        organism: Optional[str] = None,
        size: int = 100,
    ) -> List[ProteinSearchResult]:
        """
        Search for proteins by GO term or functional description.
        """
        try:
            params: Dict[str, Any] = {"size": size}
            if go_term:
                params["goTerm"] = go_term
            if function:
                params["function"] = function
            if organism:
                params["organism"] = organism

            response = await self._call_tool("search_by_function", params)
            return self._parse_protein_results(response)

        except Exception as e:
            print(f"Error searching by function: {e}")
            return []

    async def search_by_localization(
        self,
        localization: str,
        organism: Optional[str] = None,
        size: int = 100,
    ) -> List[ProteinSearchResult]:
        """
        Search for proteins by subcellular localization.
        """
        try:
            params: Dict[str, Any] = {"localization": localization, "size": size}
            if organism:
                params["organism"] = organism

            response = await self._call_tool("search_by_localization", params)
            return self._parse_protein_results(response)

        except Exception as e:
            print(f"Error searching by localization: {e}")
            return []

    # Commented out: search_by_function
    # async def search_by_function(self, function: Optional[str] = None, 
    #                            go_term: Optional[str] = None, organism: Optional[str] = None,
    #                            size: int = 10) -> List[ProteinSearchResult]:
    #     """
    #     Search proteins by GO terms or functional annotations.
    #     
    #     Args:
    #         function: Functional description or keyword
    #         go_term: Gene Ontology term (e.g., GO:0005524)
    #         organism: Organism name or taxonomy ID to filter results
    #         size: Number of results to return (1-500, default: 25)
    #         
    #     Returns:
    #         List of ProteinSearchResult objects
    #     """
    #     try:
    #         params = {"size": size}
    #         if function:
    #             params["function"] = function
    #         if go_term:
    #             params["goTerm"] = go_term
    #         if organism:
    #             params["organism"] = organism
    #             
    #         response = await self._call_tool("search_by_function", params)
    #         return self._parse_protein_results(response)
    #         
    #     except Exception as e:
    #         print(f"Error searching by function: {e}")
    #         return []
    
    # Commented out: search_by_localization
    # async def search_by_localization(self, localization: str, organism: Optional[str] = None,
    #                                 size: int = 10) -> List[ProteinSearchResult]:
    #     """
    #     Find proteins by subcellular localization.
    #     
    #     Args:
    #         localization: Subcellular localization (e.g., nucleus, mitochondria)
    #         organism: Organism name or taxonomy ID to filter results
    #         size: Number of results to return (1-500, default: 25)
    #         
    #     Returns:
    #         List of ProteinSearchResult objects
    #     """
    #     try:
    #         params = {
    #             "localization": localization,
    #             "size": size
    #         }
    #         if organism:
    #             params["organism"] = organism
    #             
    #         response = await self._call_tool("search_by_localization", params)
    #         return self._parse_protein_results(response)
    #         
    #     except Exception as e:
    #         print(f"Error searching by localization: {e}")
    #         return []
    
    # Commented out: advanced_search
    # async def advanced_search(self, query: Optional[str] = None, organism: Optional[str] = None,
    #                         min_length: Optional[int] = None, max_length: Optional[int] = None,
    #                         min_mass: Optional[float] = None, max_mass: Optional[float] = None,
    #                         keywords: Optional[List[str]] = None, size: int = 100) -> List[ProteinSearchResult]:
    #     """
    #     Complex queries with multiple filters.
    #     
    #     Args:
    #         query: Base search query
    #         organism: Organism name or taxonomy ID
    #         min_length: Minimum sequence length
    #         max_length: Maximum sequence length
    #         min_mass: Minimum molecular mass (Da)
    #         max_mass: Maximum molecular mass (Da)
    #         keywords: Array of keywords to include
    #         size: Number of results to return (1-500, default: 25)
    #         
    #     Returns:
    #         List of ProteinSearchResult objects
    #     """
    #     try:
    #         params = {"size": size}
    #         if query:
    #             params["query"] = query
    #         if organism:
    #             params["organism"] = organism
    #         if min_length:
    #             params["minLength"] = min_length
    #         if max_length:
    #             params["maxLength"] = max_length
    #         if min_mass:
    #             params["minMass"] = min_mass
    #         if max_mass:
    #             params["maxMass"] = max_mass
    #         if keywords:
    #             params["keywords"] = keywords
    #             
    #         response = await self._call_tool("advanced_search", params)
    #         return self._parse_protein_results(response)
    #         
    #     except Exception as e:
    #         print(f"Error in advanced search: {e}")
    #         return []
