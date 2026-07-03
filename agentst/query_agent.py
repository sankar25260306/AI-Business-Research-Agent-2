"""
agents/query_agent.py
Phase 1: Intelligent Query Understanding.
Takes a free-text query like "Hair Salons in Lakeway, Texas" and produces
a structured (category, location) pair plus a list of diverse search
variations the Browser Agent should run.
"""

import json
from typing import List

from loguru import logger

from core.llm_client import LocalLLM
from core.config import SEARCH

SYSTEM_PROMPT = """You are a search-strategy planner for a local business research agent.
Given a free-text user query, extract the business category and the geographic
location, then propose a diverse set of web search queries that would help
discover ALL businesses of that category in that location (not just the
obvious top results).

Always respond with strict JSON in this exact shape:
{
  "category": "<business category, singular generic form>",
  "location": "<city, state / region as written or inferred>",
  "search_variations": ["<query 1>", "<query 2>", "..."]
}

Search variation guidelines:
- Include the plain "<category> in <location>" query.
- Include "best <category> <location>", "<category> near <location>", "top rated".
- Include site-restricted queries for major directories, e.g. site:yelp.com,
  site:facebook.com, site:linkedin.com, site:yellowpages.com, site:bbb.org.
- Include at least one query for government/professional licensing directories
  if relevant to the category (e.g. state licensing board for contractors,
  doctors, lawyers).
- Produce 10-15 variations. Do not repeat near-duplicates.
"""


class QueryAgent:
    def __init__(self):
        self.llm = LocalLLM()

    def parse(self, raw_query: str) -> dict:
        result = self.llm.chat_json(SYSTEM_PROMPT, raw_query)
        if not result or "category" not in result:
            logger.warning("QueryAgent: LLM parse failed, using naive fallback split.")
            result = self._fallback_parse(raw_query)
        result.setdefault("search_variations", [])
        result["search_variations"] = self._augment_with_site_searches(
            result["category"], result["location"], result["search_variations"]
        )
        return result

    def _fallback_parse(self, raw_query: str) -> dict:
        # naive "X in Y" split if the LLM is unreachable
        if " in " in raw_query:
            category, location = raw_query.split(" in ", 1)
        else:
            category, location = raw_query, ""
        return {"category": category.strip(), "location": location.strip(), "search_variations": []}

    def _augment_with_site_searches(self, category: str, location: str, variations: List[str]) -> List[str]:
        base = f"{category} {location}".strip()
        deterministic = [
            f"{category} in {location}".strip(),
            f"best {base}".strip(),
            f"{category} near {location}".strip(),
            f"top rated {base}".strip(),
            f"{category} reviews {location}".strip(),
            f"{category} directory {location}".strip(),
        ]
        forced = [f"site:{d} {base}" for d in SEARCH.site_restricted_domains]
        merged = list(dict.fromkeys(variations + deterministic + forced))  # dedupe, keep order
        return merged

    def expand_further(self, category: str, location: str, already_tried: List[str]) -> List[str]:
        """Called mid-research when discovery plateaus - ask the LLM for fresh angles."""
        prompt = (
            f"Category: {category}\nLocation: {location}\n"
            f"Already tried search queries: {json.dumps(already_tried[-20:])}\n\n"
            "Discovery has slowed down. Propose 8 NEW, different search query "
            "strategies we haven't tried yet (different phrasing, neighborhoods/"
            "sub-areas, alternate engines, related professional associations, "
            "local news 'best of' lists, chamber of commerce listings, etc). "
            'Respond as JSON: {"search_variations": ["...", "..."]}'
        )
        result = self.llm.chat_json(SYSTEM_PROMPT, prompt)
        if result and "search_variations" in result:
            return result["search_variations"]
        base = f"{category} {location}".strip()
        return [
            f"{base} chamber of commerce",
            f"{base} local directory",
            f"{base} professional association",
            f"{base} appointments",
            f"{base} reviews",
        ]
