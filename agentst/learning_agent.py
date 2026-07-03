"""
agents/learning_agent.py
Phase 8: Autonomous Learning.
Tracks, per domain, how often it contributed a field that ended up being the
verified/winning value (vs. losing a conflict or being unused) across every
research session. Future runs read these scores to bias the Verification
Agent's domain trust upward/downward over time.
"""

import json
import time
from pathlib import Path
from typing import Dict, List
from loguru import logger

LEARNING_STORE = Path("data/source_reliability.json")


class LearningAgent:
    def __init__(self):
        LEARNING_STORE.parent.mkdir(parents=True, exist_ok=True)
        self.stats = self._load()

    def _load(self) -> Dict:
        if LEARNING_STORE.exists():
            try:
                return json.loads(LEARNING_STORE.read_text())
            except Exception:
                pass
        return {}

    def _save(self):
        LEARNING_STORE.write_text(json.dumps(self.stats, indent=2))

    def record_session(self, verified_profiles: List[Dict]):
        """Call once per research session after verification completes."""
        for profile in verified_profiles:
            winners = set(profile.get("source_urls", {}).values())
            all_sources = set(profile.get("all_sources", []))
            losers = all_sources - winners

            for url in winners:
                self._bump(url, won=True)
            for url in losers:
                self._bump(url, won=False)

        self._save()
        self._log_summary()

    def _domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace("www.", "") or "unknown"

    def _bump(self, url: str, won: bool):
        domain = self._domain(url)
        entry = self.stats.setdefault(domain, {"wins": 0, "losses": 0, "last_seen": 0})
        entry["wins" if won else "losses"] += 1
        entry["last_seen"] = time.time()

    def reliability_score(self, domain: str) -> float:
        entry = self.stats.get(domain)
        if not entry or (entry["wins"] + entry["losses"]) == 0:
            return 0.5  # neutral prior for unseen domains
        return round(entry["wins"] / (entry["wins"] + entry["losses"]), 3)

    def _log_summary(self):
        ranked = sorted(self.stats.items(), key=lambda kv: self.reliability_score(kv[0]), reverse=True)
        top = ranked[:5]
        logger.info("Learning Agent - top trusted domains so far: " +
                    ", ".join(f"{d}({self.reliability_score(d)})" for d, _ in top))
