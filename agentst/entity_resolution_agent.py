"""
agents/entity_resolution_agent.py
Phase 7: Entity Resolution & Deduplication.
Groups the raw per-page extracted profiles into clusters that represent the
SAME real-world business (e.g. "ABC Hair Studio" vs "ABC Hair & Spa" found
on different sites), using fuzzy name matching + shared phone/address/website
as strong signals, with an LLM fallback for ambiguous cases.
"""

import re
from typing import List, Dict
from rapidfuzz import fuzz
from loguru import logger

from core.llm_client import LocalLLM


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _normalize_text(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class EntityResolutionAgent:
    def __init__(self, name_threshold: int = 82):
        self.name_threshold = name_threshold
        self.llm = LocalLLM()

    def _strong_match(self, a: Dict, b: Dict) -> bool:
        phone_a, phone_b = _normalize_phone(a.get("phone")), _normalize_phone(b.get("phone"))
        if phone_a and phone_b and phone_a == phone_b:
            return True
        web_a, web_b = _normalize_text(a.get("website")), _normalize_text(b.get("website"))
        if web_a and web_b and web_a == web_b:
            return True
        addr_a, addr_b = _normalize_text(a.get("address")), _normalize_text(b.get("address"))
        if addr_a and addr_b and fuzz.ratio(addr_a, addr_b) > 90:
            return True
        return False

    def _name_match(self, a: Dict, b: Dict) -> bool:
        name_a, name_b = a.get("business_name", ""), b.get("business_name", "")
        if not name_a or not name_b:
            return False
        return fuzz.token_sort_ratio(name_a, name_b) >= self.name_threshold

    def _llm_tiebreak(self, a: Dict, b: Dict) -> bool:
        prompt = (
            f"Business A: name='{a.get('business_name')}', address='{a.get('address')}', "
            f"phone='{a.get('phone')}', website='{a.get('website')}'\n"
            f"Business B: name='{b.get('business_name')}', address='{b.get('address')}', "
            f"phone='{b.get('phone')}', website='{b.get('website')}'\n\n"
            'Are these the SAME real-world business? Respond JSON: {"same": true/false}'
        )
        result = self.llm.chat_json("You are an entity-resolution assistant.", prompt)
        return bool(result and result.get("same") is True)

    def cluster(self, profiles: List[Dict]) -> List[List[Dict]]:
        clusters: List[List[Dict]] = []

        for profile in profiles:
            placed = False
            for cluster in clusters:
                rep = cluster[0]
                if self._strong_match(rep, profile) or self._name_match(rep, profile):
                    cluster.append(profile)
                    placed = True
                    break
                # ambiguous case: similar-ish name but no strong signal -> ask LLM
                if rep.get("business_name") and profile.get("business_name"):
                    similarity = fuzz.token_sort_ratio(rep["business_name"], profile["business_name"])
                    if 60 <= similarity < self.name_threshold:
                        if self._llm_tiebreak(rep, profile):
                            cluster.append(profile)
                            placed = True
                            break
            if not placed:
                clusters.append([profile])

        logger.info(f"Entity resolution: {len(profiles)} raw profiles -> {len(clusters)} unique businesses")
        return clusters
