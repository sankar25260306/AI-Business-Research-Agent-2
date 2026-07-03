"""
agents/discovery_orchestrator.py
Phase 3: Complete Business Discovery.
Drives the BrowserAgent across search rounds, expanding query variations via
the QueryAgent whenever discovery plateaus, and stops only when several
consecutive rounds produce no new unique business URLs - matching the
spec's "stop only when no additional unique businesses can be found" rule.
"""

import asyncio
import time
from typing import List, Dict
from urllib.parse import urlparse

from loguru import logger

from agents.browser_agent import BrowserAgent
from agents.query_agent import QueryAgent
from core.config import SEARCH


def _normalize_url(url: str) -> str:
    p = urlparse(url)
    return f"{p.netloc}{p.path}".rstrip("/").lower()


class DiscoveryOrchestrator:
    def __init__(self, browser: BrowserAgent, query_agent: QueryAgent):
        self.browser = browser
        self.query_agent = query_agent
        self.discovered: Dict[str, Dict] = {}  # normalized_url -> {url, title, source_query}

    async def run(
        self,
        category: str,
        location: str,
        initial_variations: List[str],
        on_new=None,
        deadline: float | None = None,
    ) -> List[Dict]:
        tried_queries: List[str] = []
        plateau_rounds = 0
        round_num = 0
        queue = list(initial_variations)

        while (
            queue
            and round_num < SEARCH.max_search_rounds
            and plateau_rounds < SEARCH.plateau_rounds_to_stop
            and (deadline is None or time.monotonic() < deadline)
        ):
            round_num += 1
            query = queue.pop(0)
            tried_queries.append(query)
            engine = SEARCH.engines[(round_num - 1) % len(SEARCH.engines)]

            logger.info(f"[Round {round_num}] engine={engine} query='{query}'")
            results = await self.browser.search_engine(engine, query, SEARCH.max_results_per_query)

            new_count = 0
            for r in results:
                key = _normalize_url(r["url"])
                if key and key not in self.discovered:
                    self.discovered[key] = {**r, "source_query": query, "engine": engine}
                    new_count += 1
                    if on_new:
                        await on_new(self.discovered[key])

            logger.info(f"[Round {round_num}] +{new_count} new businesses (total={len(self.discovered)})")

            if new_count == 0:
                plateau_rounds += 1
            else:
                plateau_rounds = 0

            # when the queue runs dry or we're plateauing, ask the LLM for fresh angles
            if (
                (not queue or plateau_rounds == SEARCH.plateau_rounds_to_stop - 1)
                and (deadline is None or time.monotonic() < deadline)
            ):
                fresh = await asyncio.to_thread(
                    self.query_agent.expand_further, category, location, tried_queries
                )
                fresh = [q for q in fresh if q not in tried_queries]
                queue.extend(fresh)
                if fresh:
                    logger.info(f"Expanded search with {len(fresh)} new query strategies.")

            await asyncio.sleep(0.5)  # be a polite, human-paced browser

        logger.info(
            f"Discovery stopped after {round_num} rounds / {len(tried_queries)} queries. "
            f"Total unique candidates: {len(self.discovered)}"
        )
        return list(self.discovered.values())