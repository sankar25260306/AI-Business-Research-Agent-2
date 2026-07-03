"""
agents/verification_agent.py
Phase 5 + 6: Cross-Source Verification & Intelligent Conflict Resolution.

Works AFTER the discovery+extraction phase, on the in-memory list of raw
extracted profiles (which may contain several entries for the same business,
pulled from different source pages). For each field, it looks at every value
seen across sources and decides a verified value + confidence score, using
source reliability, frequency, and (if conflicting) explains why it picked
the winner.
"""

from collections import defaultdict
import json
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse

from loguru import logger

# crude but effective domain authority ranking - tune as you learn more
DOMAIN_TRUST = {
    "official_website": 1.0,   # detected separately, see _is_official_site
    "google.com": 0.95,
    "yelp.com": 0.85,
    "bbb.org": 0.85,
    "facebook.com": 0.75,
    "linkedin.com": 0.75,
    "yellowpages.com": 0.6,
    "default": 0.5,
}

VERIFIABLE_FIELDS = ["phone", "address", "email", "website", "working_hours",
                     "rating", "license_information"]


class VerificationAgent:
    def __init__(self, learning_store: str = "data/source_reliability.json"):
        self.learning_store = Path(learning_store)
        self.learned_reliability = self._load_learned_reliability()

    def _load_learned_reliability(self) -> Dict[str, float]:
        if not self.learning_store.exists():
            return {}
        try:
            stats = json.loads(self.learning_store.read_text())
        except Exception:
            return {}
        scores = {}
        for domain, entry in stats.items():
            total = entry.get("wins", 0) + entry.get("losses", 0)
            if total:
                scores[domain] = entry.get("wins", 0) / total
        return scores

    def _domain_trust(self, url: str, business_website: str = "") -> float:
        domain = urlparse(url).netloc.replace("www.", "")
        if business_website and domain in business_website:
            static_trust = DOMAIN_TRUST["official_website"]
        else:
            static_trust = DOMAIN_TRUST.get(domain, DOMAIN_TRUST["default"])
        learned = self.learned_reliability.get(domain)
        if learned is None:
            return static_trust
        return round((static_trust * 0.7) + (learned * 0.3), 3)

    def verify_group(self, same_business_profiles: List[Dict]) -> Dict:
        """
        Takes multiple raw profiles believed to be the SAME business (already
        grouped by EntityResolutionAgent) and merges them into one verified
        profile with per-field confidence + evidence + conflict reasoning.
        """
        merged = {}
        field_sources: Dict[str, List[Dict]] = defaultdict(list)
        field_evidence: Dict[str, List[Dict]] = defaultdict(list)
        confidence: Dict[str, float] = {}
        conflicts: Dict[str, str] = {}
        verification_status: Dict[str, str] = {}

        # pick a representative website to detect "official site" trust boost
        website = next((p.get("website") for p in same_business_profiles if p.get("website")), "")

        for field in VERIFIABLE_FIELDS:
            value_votes: Dict[str, List[Dict]] = defaultdict(list)
            for p in same_business_profiles:
                raw_val = p.get(field) or ""
                val = "; ".join(str(item).strip() for item in raw_val if item) if isinstance(raw_val, list) else str(raw_val).strip()
                src = p.get("_raw_source", "")
                if val:
                    value_votes[val].append({"source": src, "trust": self._domain_trust(src, website)})

            if not value_votes:
                merged[field] = ""
                confidence[field] = 0.0
                verification_status[field] = "not_found"
                continue

            if len(value_votes) == 1:
                value, evidence = next(iter(value_votes.items()))
                merged[field] = value
                confidence[field] = round(min(0.6 + 0.13 * len(evidence), 0.99), 2)
                field_sources[field] = evidence
                field_evidence[field] = [{"value": value, **item} for item in evidence]
                verification_status[field] = "verified" if len(evidence) > 1 else "single_source"
            else:
                # CONFLICT - score each candidate by frequency * avg trust
                scored = []
                for value, evidence in value_votes.items():
                    freq = len(evidence)
                    avg_trust = sum(e["trust"] for e in evidence) / freq
                    score = freq * avg_trust
                    scored.append((score, value, evidence, freq, avg_trust))
                scored.sort(reverse=True, key=lambda x: x[0])

                best_score, best_value, best_evidence, freq, avg_trust = scored[0]
                runner_up_score = scored[1][0] if len(scored) > 1 else 0

                merged[field] = best_value
                field_sources[field] = best_evidence
                # confident win if clearly ahead of runner-up, else flag for manual review
                if best_score - runner_up_score < 0.15:
                    merged[field] = "Requires Manual Verification"
                    confidence[field] = 0.0
                    verification_status[field] = "requires_manual_verification"
                    conflicts[field] = (
                        f"Conflicting values found ({', '.join(v for _, v, *_ in scored)}); "
                        f"no source was clearly more reliable - flagged for manual review."
                    )
                else:
                    confidence[field] = round(min(0.5 + 0.1 * freq + 0.3 * avg_trust, 0.99), 2)
                    verification_status[field] = "verified_with_conflict_resolution"
                    conflicts[field] = (
                        f"'{best_value}' chosen over {len(scored)-1} alternative(s): "
                        f"appeared on {freq} source(s) with avg domain trust {avg_trust:.2f}."
                    )
                for _, value, evidence, _, _ in scored:
                    field_evidence[field].extend({"value": value, **item} for item in evidence)

        # non-verifiable / list-type fields: union across all profiles
        for field in ["services", "specialties", "certifications", "awards",
                       "team_members", "accepted_payments", "social_profiles",
                       "images_urls", "videos_urls", "faq"]:
            union = []
            for p in same_business_profiles:
                for item in (p.get(field) or []):
                    if item not in union:
                        union.append(item)
            merged[field] = union

        merged["business_name"] = same_business_profiles[0].get("business_name", "")
        for field in ["owner_name", "years_in_business", "insurance_information",
                      "appointment_booking_url", "business_description"]:
            merged[field] = next(
                (p.get(field) for p in same_business_profiles if p.get(field)), ""
            )

        for field, value in list(merged.items()):
            if field in confidence or field.startswith("_"):
                continue
            has_value = bool(value)
            confidence[field] = 0.55 if has_value else 0.0
            verification_status[field] = "collected_unverified" if has_value else "not_found"

        merged["source_urls"] = {f: field_sources[f][0]["source"] for f in field_sources if field_sources[f]}
        if "license_information" in merged["source_urls"]:
            merged["source_urls"]["license"] = merged["source_urls"]["license_information"]
        merged["field_evidence"] = dict(field_evidence)
        merged["field_confidence"] = confidence
        merged["verification_status"] = verification_status
        merged["conflict_resolution"] = conflicts
        merged["verification_score"] = round(
            sum(confidence.values()) / max(len(confidence), 1) * 100, 1
        )
        merged["all_sources"] = list({p.get("_raw_source", "") for p in same_business_profiles if p.get("_raw_source")})

        logger.info(
            f"Verified '{merged['business_name']}' - score={merged['verification_score']} "
            f"conflicts={len(conflicts)}"
        )
        return merged
