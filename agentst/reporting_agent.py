"""
agents/reporting_agent.py
Generates the final research report matching the spec's "Final Research
Report" format, plus CSV export.
"""

import csv
import io
import time
from typing import List, Dict


class ReportingAgent:
    def generate(self, query: str, category: str, location: str,
                 raw_candidate_count: int, raw_profile_count: int,
                 verified_profiles: List[Dict], pages_crawled: int,
                 websites_visited: int, search_variations: int,
                 duplicates_merged: int, duration_seconds: float,
                 cache_hits: int = 0) -> Dict:
        avg_conf = round(
            sum(p.get("verification_score", 0) for p in verified_profiles) /
            max(len(verified_profiles), 1), 1
        )
        manual_review = sum(
            1
            for p in verified_profiles
            for field in ["phone", "address", "email"]
            if p.get(field) == "Requires Manual Verification"
        )

        minutes, seconds = divmod(int(duration_seconds), 60)

        return {
            "query": query,
            "category": category,
            "location": location,
            "businesses_discovered": raw_candidate_count,
            "profiles_extracted": raw_profile_count,
            "businesses_fully_verified": len(verified_profiles),
            "cache_hits": cache_hits,
            "duplicate_records_merged": duplicates_merged,
            "websites_visited": websites_visited,
            "search_variations_generated": search_variations,
            "pages_crawled": pages_crawled,
            "average_verification_confidence": f"{avg_conf}%",
            "fields_requiring_manual_review": manual_review,
            "research_duration": f"{minutes} Minutes {seconds} Seconds",
            "research_duration_seconds": duration_seconds,
            "generated_at": time.time(),
            "businesses": verified_profiles,
        }

    def to_csv(self, businesses: List[Dict]) -> str:
        if not businesses:
            return ""
        buf = io.StringIO()
        fieldnames = ["business_name", "address", "phone", "email", "website",
                      "working_hours", "rating", "review_count", "verification_score"]
        writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for b in businesses:
            writer.writerow(b)
        return buf.getvalue()
