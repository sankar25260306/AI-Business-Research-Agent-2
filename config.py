"""
core/config.py
Central configuration for the AI Business Research Agent.

IMPORTANT: This project intentionally avoids every API listed as prohibited
in the challenge spec (Google Places, Yelp Fusion, RapidAPI, scrape.do,
OpenStreetMap/Overpass, any paid/commercial business or scraping API).
All discovery happens through a real, visible Playwright browser hitting
public search engines and public web pages. All LLM reasoning runs on a
locally hosted model via Ollama (or any OpenAI-compatible local server).
"""

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class LLMConfig:
    # Local model served by Ollama. Change model name to whatever you've pulled,
    # e.g. "qwen2.5:7b-instruct" or "llama3.1:8b".
    provider: str = "ollama"
    base_url: str = os.getenv("LOCAL_LLM_URL", "http://localhost:11434")
    model: str = os.getenv("LOCAL_LLM_MODEL", "qwen2.5:3b-instruct")
    temperature: float = 0.1
    # 20s was far too short for a 3B model doing JSON extraction on CPU -
    # nearly every call was timing out and silently dropping the profile.
    request_timeout: int = int(os.getenv("LOCAL_LLM_TIMEOUT", "120"))
    # How long to keep the model resident in memory between calls so it
    # doesn't get unloaded/reloaded (which adds huge cold-start latency).
    keep_alive: str = os.getenv("LOCAL_LLM_KEEP_ALIVE", "30m")
    # Max characters of page text sent to the LLM per extraction call.
    # Full raw HTML pages are often 50k+ chars, which massively slows
    # generation and eats context; we clean+truncate before sending.
    max_input_chars: int = int(os.getenv("LOCAL_LLM_MAX_INPUT_CHARS", "6000"))
    # How many extraction calls to run concurrently via asyncio.to_thread.
    # Ollama serializes requests to one model anyway, but this lets browser
    # I/O and other async work continue instead of blocking the event loop.
    max_concurrent_extractions: int = int(os.getenv("LOCAL_LLM_CONCURRENCY", "2"))
    max_retries: int = int(os.getenv("LOCAL_LLM_MAX_RETRIES", "2"))


@dataclass
class BrowserConfig:
    headless: bool = os.getenv("BROWSER_HEADLESS", "false").lower() in {"1", "true", "yes"}
    slow_mo_ms: int = 50            # tiny delay so actions are human-watchable
    nav_timeout_ms: int = int(os.getenv("BROWSER_NAV_TIMEOUT_MS", "12000"))
    captcha_timeout_ms: int = int(os.getenv("CAPTCHA_TIMEOUT_MS", "240000"))
    user_data_dir: str = os.getenv("BROWSER_PROFILE_DIR", "data/browser_profile")
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    viewport: dict = field(default_factory=lambda: {"width": 1366, "height": 850})


@dataclass
class SearchConfig:
    # Public search engines only - no API keys, no paid search APIs.
    engines: List[str] = field(default_factory=lambda: ["duckduckgo", "bing", "google"])
    site_restricted_domains: List[str] = field(default_factory=lambda: [
        "yelp.com", "facebook.com", "linkedin.com", "yellowpages.com",
        "bbb.org", "mapquest.com",
    ])
    max_results_per_query: int = int(os.getenv("MAX_RESULTS_PER_QUERY", "15"))
    # Was capping at 4 rounds with a 2-round plateau, so a single 0-new-result
    # round (e.g. a Bing SERP parsing hiccup) would end discovery early even
    # though the very next round often found more. Raised both.
    max_search_rounds: int = int(os.getenv("MAX_SEARCH_ROUNDS", "12"))
    plateau_rounds_to_stop: int = int(os.getenv("PLATEAU_ROUNDS_TO_STOP", "3"))
    # This used to be a hard cap of 8 - meaning no matter how many businesses
    # discovery found, only 8 pages ever got fetched/extracted per run. That
    # single line was the biggest bottleneck between this system and the
    # spec's "198 discovered / 191 verified" scale. Now acts as a safety
    # ceiling only (protects against a pathologically huge discovery list),
    # not a real-world cap - raise further via env if needed.
    max_pages_to_fetch: int = int(os.getenv("MAX_PAGES_TO_FETCH", "500"))
    # How many browser tabs fetch pages concurrently. Each fetch_page_html()
    # call opens its own independent Playwright Page, so this is safe to
    # parallelize - previously pages were fetched one at a time in a for
    # loop, which is why "Pages Crawled" stayed in the single digits.
    max_concurrent_page_fetches: int = int(os.getenv("MAX_CONCURRENT_PAGE_FETCHES", "6"))
    # Was 240s (4 min) - too short to do meaningful discovery AND extraction
    # at any real scale. Raised so a full multi-hundred-business run has
    # room to actually finish instead of always hitting "time budget
    # reached" partway through extraction.
    max_runtime_seconds: int = int(os.getenv("RESEARCH_MAX_RUNTIME_SECONDS", "900"))
    discovery_timeout_seconds: int = int(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "180"))


@dataclass
class StorageConfig:
    sqlite_path: str = os.getenv("DB_PATH", "data/research.db")
    screenshot_dir: str = "data/evidence_screenshots"


LLM = LLMConfig()
BROWSER = BrowserConfig()
SEARCH = SearchConfig()
STORAGE = StorageConfig()

# Canonical business profile field list - mirrors the spec's expected JSON output
BUSINESS_FIELDS = [
    "business_name", "address", "phone", "email", "website", "working_hours",
    "rating", "review_count", "services", "specialties", "license_information",
    "certifications", "awards", "owner_name", "team_members", "years_in_business",
    "insurance_information", "accepted_payments", "appointment_booking_url",
    "social_profiles", "images_urls", "videos_urls", "business_description", "faq",
]