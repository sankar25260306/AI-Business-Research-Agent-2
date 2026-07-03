"""
core/pipeline.py
The complete end-to-end pipeline, used by both the CLI (main.py) and the
FastAPI/WebSocket server (server.py). Accepts an optional async `emit`
callback for real-time streaming (Phase 10).
"""

import asyncio
import time
from typing import Callable, Optional
from urllib.parse import urlparse

from loguru import logger

from agents.browser_agent import BrowserAgent
from agents.query_agent import QueryAgent
from agents.discovery_orchestrator import DiscoveryOrchestrator
from agents.extraction_agent import ExtractionAgent
from agents.entity_resolution_agent import EntityResolutionAgent
from agents.verification_agent import VerificationAgent
from agents.learning_agent import LearningAgent
from agents.reporting_agent import ReportingAgent
from core.config import SEARCH, LLM
from core.storage import Storage


def _cache_key(name: str, location: str) -> str:
    return f"{name.strip().lower()}|{location.strip().lower()}"


def _keywords(text: str) -> set[str]:
    words = []
    for raw in text.lower().replace("-", " ").split():
        word = "".join(ch for ch in raw if ch.isalnum())
        if len(word) >= 4 and word not in {"near", "best", "rated", "directory", "reviews"}:
            words.append(word)
    return set(words)


def _prioritize_candidates(candidates: list[dict], raw_query: str, category: str, location: str) -> list[dict]:
    query_terms = _keywords(raw_query)
    location_terms = _keywords(location)
    category_terms = _keywords(category)
    specific_terms = query_terms - location_terms - category_terms

    def score(candidate: dict) -> tuple[int, str]:
        haystack = f"{candidate.get('title', '')} {candidate.get('url', '')}".lower()
        hits = sum(1 for term in query_terms if term in haystack)
        specific_hits = sum(1 for term in specific_terms if term in haystack)
        list_penalty = sum(1 for word in ("list", "top", "best", "guide", "directory") if word in haystack)
        return ((specific_hits * 10) + (hits * 2) - list_penalty, haystack)

    return sorted(candidates, key=score, reverse=True)


async def run_pipeline(raw_query: str, emit: Optional[Callable] = None) -> dict:
    """
    emit(event_type: str, message: str, progress: float, data: dict | None)
    is called at every meaningful step for live streaming to a UI.
    """
    async def _emit(event_type, message, progress, data=None):
        logger.info(f"[{progress:>5.1f}%] {message}")
        if emit:
            await emit(event_type, message, progress, data)

    t0 = time.time()
    deadline = time.monotonic() + SEARCH.max_runtime_seconds if SEARCH.max_runtime_seconds > 0 else None
    query_agent = QueryAgent()
    browser = BrowserAgent()
    extractor = ExtractionAgent()
    resolver = EntityResolutionAgent()
    verifier = VerificationAgent()
    learner = LearningAgent()
    reporter = ReportingAgent()
    storage = Storage()

    await _emit("status", "Parsing query with local LLM...", 2)
    parsed = await asyncio.to_thread(query_agent.parse, raw_query)
    category, location = parsed["category"], parsed["location"]
    await _emit("status", f"Category='{category}' | Location='{location}'", 5,
                {"category": category, "location": location})

    search_id = storage.save_search(raw_query, category, location)
    cached_profiles = storage.cached_profiles(category, location)
    cached_keys = {_cache_key(p.get("business_name", ""), location) for p in cached_profiles}
    for profile in cached_profiles:
        await _emit("business_cached", f"Cache hit: {profile.get('business_name', 'Unknown')}", 6, profile)

    raw_profiles = []
    websites_visited = set()

    try:
        await browser.start()
        orchestrator = DiscoveryOrchestrator(browser, query_agent)

        async def on_new_candidate(candidate):
            await _emit("discovery", f"Found candidate: {candidate['title'][:60]}", 25,
                        {"url": candidate["url"], "title": candidate["title"]})

        await _emit("status", "Starting autonomous browser discovery...", 8)
        discovery_deadline = None
        if deadline is not None:
            discovery_deadline = min(deadline, time.monotonic() + SEARCH.discovery_timeout_seconds)
        candidates = await orchestrator.run(
            category, location, parsed["search_variations"], on_new=on_new_candidate, deadline=discovery_deadline
        )
        await _emit("status", f"Discovery complete: {len(candidates)} unique candidate URLs", 30,
                    {"candidate_count": len(candidates)})

        # Phase 4: extraction
        # Previously this fetched and extracted ONE page at a time in a
        # sequential for loop, sliced down to only the top 8 candidates.
        # That's why "Pages Crawled" and "Businesses Discovered" stayed in
        # the single digits regardless of how much discovery found. Now:
        #   - every discovered candidate (up to the safety ceiling
        #     SEARCH.max_pages_to_fetch) is eligible for extraction, not
        #     just the top 8.
        #   - fetching happens across multiple concurrent browser tabs
        #     (each fetch_page_html() call opens its own independent
        #     Playwright Page, so this is safe).
        #   - extraction (the LLM call) happens across multiple concurrent
        #     worker threads, bounded separately since LLM inference is the
        #     slower, more serialized resource.
        candidates_to_fetch = _prioritize_candidates(candidates, raw_query, category, location)[:SEARCH.max_pages_to_fetch]
        total_candidates = max(len(candidates_to_fetch), 1)
        completed_count = 0

        browser_sem = asyncio.Semaphore(max(1, SEARCH.max_concurrent_page_fetches))
        llm_sem = asyncio.Semaphore(max(1, LLM.max_concurrent_extractions))

        async def _process_candidate(cand: dict):
            nonlocal completed_count
            if deadline is not None and deadline - time.monotonic() < 15:
                return  # out of time budget - don't start new work

            url = cand["url"]
            async with browser_sem:
                if deadline is not None and time.monotonic() > deadline:
                    return
                html = await browser.fetch_page_html(url)
            if not html:
                return
            websites_visited.add(urlparse(url).netloc)

            async with llm_sem:
                if deadline is not None and time.monotonic() > deadline:
                    return
                try:
                    profile = await asyncio.to_thread(extractor.extract, html, url, category, location)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Extraction failed for {url}: {e}")
                    return

            completed_count += 1
            progress = 30 + (completed_count / total_candidates) * 35
            if not profile:
                return

            cache_key = _cache_key(profile["business_name"], location)
            if cache_key in cached_keys:
                await _emit("cache_skip", f"Skipped cached business: {profile['business_name']}", progress, profile)
                return

            raw_profiles.append(profile)
            await _emit("business_extracted", profile["business_name"], progress, profile)

        if deadline is not None and deadline - time.monotonic() < 15:
            await _emit("status", "Research time budget reached; finishing with collected results.", 66)
        else:
            await asyncio.gather(*(_process_candidate(cand) for cand in candidates_to_fetch))

        # Phase 7: entity resolution / dedup
        await _emit("status", f"Resolving {len(raw_profiles)} raw profiles into unique businesses...", 68)
        # resolver.cluster() can call the local LLM (_llm_tiebreak) for
        # ambiguous name matches - same blocking risk as extraction above.
        clusters = await asyncio.to_thread(resolver.cluster, raw_profiles)
        duplicates_merged = len(raw_profiles) - len(clusters)

        # Phase 5/6: verification + conflict resolution
        await _emit("status", "Cross-source verification + conflict resolution...", 75)
        verified = []
        for cluster in clusters:
            merged = verifier.verify_group(cluster)
            key = _cache_key(merged["business_name"], location)
            storage.save_business(search_id, key, merged, merged.get("verification_score", 0) / 100)
            storage.save_cached_profile(category, location, key, merged, merged.get("verification_score", 0) / 100)
            verified.append(merged)
            await _emit("business_verified", merged["business_name"], 80, merged)

        combined_verified = _merge_cached_and_fresh(cached_profiles, verified, location)

        # Phase 8: learning
        learner.record_session(combined_verified)
        await _emit("status", "Updated source-reliability scores for future runs.", 90)

        # Final report
        duration = time.time() - t0
        report = reporter.generate(
            query=raw_query, category=category, location=location,
            raw_candidate_count=len(candidates), raw_profile_count=len(raw_profiles),
            verified_profiles=combined_verified, pages_crawled=browser.pages_crawled,
            websites_visited=len(websites_visited),
            search_variations=len(parsed["search_variations"]),
            duplicates_merged=duplicates_merged, duration_seconds=duration,
            cache_hits=len(cached_profiles),
        )
        await _emit("complete", "Research complete.", 100, report)
        return report

    finally:
        await browser.stop()


def _merge_cached_and_fresh(cached_profiles: list[dict], fresh_profiles: list[dict], location: str) -> list[dict]:
    merged: dict[str, dict] = {}
    for profile in cached_profiles + fresh_profiles:
        key = _cache_key(profile.get("business_name", ""), location)
        if not key:
            continue
        existing = merged.get(key)
        if not existing or profile.get("verification_score", 0) >= existing.get("verification_score", 0):
            merged[key] = profile
    return list(merged.values())