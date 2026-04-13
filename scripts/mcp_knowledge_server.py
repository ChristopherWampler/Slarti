#!/usr/bin/env python3
"""
mcp_knowledge_server.py — MCP server for Slarti regional knowledge search

Exposes search_knowledge as a tool via Model Context Protocol (MCP).
Launched by OpenClaw via .mcp.json — runs as a stdio MCP server.

The tool searches 390+ chunks from MU Extension, Farmer's Almanac,
and the plant database stored in pgvector.
"""

import sys
import pathlib

# Add scripts dir to path so we can import pgvector_search
SCRIPT_DIR = pathlib.Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from dotenv import load_dotenv
load_dotenv(dotenv_path=SCRIPT_DIR.parent / '.env')

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("slarti-knowledge")


@mcp.tool()
def search_knowledge(query: str, limit: int = 5) -> str:
    """Search Slarti's regional knowledge base for Farmington, MO gardening information.

    Searches 390+ chunks from MU Extension publications, the Old Farmer's Almanac
    (Farmington planting calendar), and the plant database. Returns the most
    relevant results with source attribution.

    Use this tool for any question about:
    - Planting timing, frost dates, or seasonal scheduling
    - Pest identification or treatment
    - Soil amendments or fertilization
    - Plant recommendations for Zone 6b
    - Pruning, harvesting, or care techniques

    Args:
        query: Natural language search query (e.g., "when to plant tomatoes in Farmington")
        limit: Maximum number of results to return (default 5)
    """
    from pgvector_search import search_knowledge as _search

    results = _search(query=query, limit=limit)

    if not results:
        return "No matching knowledge found. Answer from general Zone 6b knowledge."

    output_parts = []
    for r in results:
        source = r.get('source_id', 'unknown')
        title = r.get('title', '')
        content = r.get('content', '')
        url = r.get('source_url', '')
        similarity = r.get('similarity')
        authority = r.get('authority_score', 0)

        entry = f"[{source}] {title}\n{content}"
        if url:
            entry += f"\nSource URL: {url}"
        if similarity:
            entry += f"\n(relevance: {similarity:.2f}, authority: {authority})"
        output_parts.append(entry)

    return "\n\n---\n\n".join(output_parts)


if __name__ == "__main__":
    mcp.run(transport="stdio")
