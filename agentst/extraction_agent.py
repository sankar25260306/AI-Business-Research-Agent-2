"""
agents/extraction_agent.py
Phase 4: Business Profile Extraction.
Reads the visible text of a page (already fetched by BrowserAgent) and asks
the LOCAL LLM to extract only what is explicitly present. Every extracted
field is tagged with the source_url it came from, never invented.
"""

import re
from typing import Optional
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from loguru import logger

from core.llm_client import LocalLLM
from core.config import BUSINESS_FIELDS, LLM

SYSTEM_PROMPT = f"""You are a strict information-extraction engine for local
business web pages. You will be given visible page text plus extracted links
and media URLs from a single web page.

Extract ONLY information that is explicitly written on the page. NEVER guess,
estimate, or invent a value. If a field is not present on the page, leave it
as an empty string "" or empty list [].

Return strict JSON with exactly these keys:
{BUSINESS_FIELDS}

Rules:
- "services" and "specialties" are lists of short strings.
- "rating" is the numeric rating as written (e.g. "4.7"), "review_count" is the
  number of reviews as written.
- "social_profiles" is a list of full URLs found on the page (facebook,
  instagram, linkedin, etc).
- Do not fabricate a phone number, address, or email under any circumstance.
- If the page is a bot-block / CAPTCHA / "access denied" page rather than a
  real business page, respond with every field empty rather than guessing.
- If the page is a "best of" list, article, or directory hub covering MANY
  businesses rather than one specific business, respond with every field
  empty rather than picking one business or naming the list/site itself.
"""

# Signals that a fetched page is a bot-block / CAPTCHA / error interstitial,
# not real business content. Checked before any extraction is attempted so
# we don't hand a "Sorry, you have been blocked" page to the LLM or fallback
# regexes, which previously produced garbage profiles (e.g. an IP address
# mis-parsed as a phone number).
BLOCKED_PAGE_SIGNALS = (
    "sorry, you have been blocked",
    "access denied",
    "are you a human",
    "are you a robot",
    "unusual traffic from your computer network",
    "please verify you are a human",
    "attention required! | cloudflare",
    "request blocked",
    "pardon our interruption",
    "checking your browser before accessing",
)

# Phrases that mark a page as a listicle/article/directory hub covering many
# businesses (e.g. "Find the Best Dentists in Austin, TX") rather than one
# specific business - using these as a business_name is always wrong.
LISTICLE_NAME_MARKERS = (
    "best ", "top 10", "top ten", "top rated", "find the best", " vs ",
    "how to choose", "guide to", "list of",
)

# Matches NANP-style phone numbers (###) ###-#### / ###-###-#### / ###.###.####
# with optional +1. The old pattern (`\d[\d\s().-]{7,}\d`) matched almost any
# long digit string, including IP addresses like "157.50.13.16".
PHONE_RE = re.compile(
    r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"
)


class ExtractionAgent:
    def __init__(self):
        self.llm = LocalLLM()

    def _page_context(self, html: str, source_url: str, max_chars: int = None) -> str:
        # Was hardcoded to 12000 here, then silently re-truncated to 6000 by
        # llm_client.py - two limits disagreeing wastes BeautifulSoup work
        # for content that never reaches the model anyway. Single source of
        # truth now lives in core.config.LLM.max_input_chars.
        max_chars = max_chars or LLM.max_input_chars
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())

        links = []
        for a in soup.select("a[href]")[:120]:
            href = urljoin(source_url, a.get("href", ""))
            label = " ".join(a.get_text(" ").split())[:80]
            if href.startswith(("http://", "https://", "mailto:", "tel:")):
                links.append(f"{label} -> {href}" if label else href)

        media = []
        for tag in soup.select("img[src], video[src], source[src]")[:80]:
            src = urljoin(source_url, tag.get("src", ""))
            if src.startswith(("http://", "https://")):
                media.append(src)

        context = [
            "VISIBLE_TEXT:",
            text,
            "\nLINKS:",
            "\n".join(dict.fromkeys(links)),
            "\nMEDIA_URLS:",
            "\n".join(dict.fromkeys(media)),
        ]
        return "\n".join(context)[:max_chars]

    def _empty_profile(self) -> dict:
        list_fields = {
            "services", "specialties", "certifications", "awards", "team_members",
            "accepted_payments", "social_profiles", "images_urls", "videos_urls", "faq",
        }
        return {field: [] if field in list_fields else "" for field in BUSINESS_FIELDS}

    def _fallback_extract(self, html: str, source_url: str, category: str = "", location: str = "") -> Optional[dict]:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        if len(text) < 40:
            return None

        profile = self._empty_profile()
        junk_names = {location.strip().lower(), category.strip().lower()} - {""}
        name_candidates = [
            soup.select_one("meta[property='og:site_name']"),
            soup.select_one("meta[property='og:title']"),
            soup.select_one("h1"),
            soup.select_one("title"),
        ]
        for item in name_candidates:
            value = ""
            if item:
                value = item.get("content", "") if item.name == "meta" else item.get_text(" ", strip=True)
            value = value.split("|")[0].split("-")[0].strip()
            # Skip candidates that look like a listicle/article title
            # ("Find the Best Dentists in Austin, TX") rather than an
            # actual business name - these were being extracted verbatim
            # as the "business" before.
            if value and any(marker in value.lower() for marker in LISTICLE_NAME_MARKERS):
                continue
            # Skip candidates that are just the bare location or category
            # (e.g. a directory hub page whose <h1> is literally "Austin").
            if value and value.strip().lower() in junk_names:
                continue
            if value:
                profile["business_name"] = value
                break

        email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
        phone_match = PHONE_RE.search(text)
        if email_match:
            profile["email"] = email_match.group(0)
        if phone_match:
            profile["phone"] = phone_match.group(0).strip()

        parsed = urlparse(source_url)
        if parsed.scheme and parsed.netloc:
            profile["website"] = f"{parsed.scheme}://{parsed.netloc}"

        social = []
        for a in soup.select("a[href]")[:120]:
            href = urljoin(source_url, a.get("href", ""))
            if href.startswith("mailto:") and not profile["email"]:
                profile["email"] = href.replace("mailto:", "").split("?")[0]
            elif href.startswith("tel:") and not profile["phone"]:
                profile["phone"] = href.replace("tel:", "").strip()
            elif href.startswith(("http://", "https://")):
                if any(domain in href for domain in ("facebook.com", "instagram.com", "linkedin.com", "youtube.com")):
                    social.append(href)

        images = []
        for tag in soup.select("img[src]")[:30]:
            src = urljoin(source_url, tag.get("src", ""))
            if src.startswith(("http://", "https://")):
                images.append(src)

        profile["social_profiles"] = list(dict.fromkeys(social))
        profile["images_urls"] = list(dict.fromkeys(images))
        if not profile["business_name"]:
            domain_name = parsed.netloc.replace("www.", "").split(".")[0].replace("-", " ").strip()
            profile["business_name"] = domain_name.title()

        if not any(profile.get(field) for field in ("business_name", "phone", "email", "website")):
            return None

        profile["source_urls"] = {
            field: source_url
            for field in ["phone", "email", "website"]
            if profile.get(field)
        }
        profile["_raw_source"] = source_url
        return profile

    def extract(self, html: str, source_url: str, category: str = "", location: str = "") -> Optional[dict]:
        if not html:
            return None
        page_context = self._page_context(html, source_url)
        if len(page_context) < 40:
            return None

        # Bot-block / CAPTCHA / error interstitial pages aren't business
        # pages at all - bail out before wasting an LLM call or letting the
        # regex fallback mine garbage like an IP address out of them.
        lowered_start = page_context[:3000].lower()
        if any(signal in lowered_start for signal in BLOCKED_PAGE_SIGNALS):
            logger.info(f"Skipping {source_url}: looks like a bot-block/error page, not a business page.")
            return None

        user_prompt = f"PAGE URL: {source_url}\n\nPAGE CONTEXT:\n{page_context}"
        result = self.llm.chat_json(SYSTEM_PROMPT, user_prompt)
        if not result:
            return self._fallback_extract(html, source_url, category, location)

        # normalize + attach source attribution for every non-empty field
        profile = {field: result.get(field, "" if field not in
                   ("services", "specialties", "certifications", "awards",
                    "team_members", "accepted_payments", "social_profiles",
                    "images_urls", "videos_urls", "faq") else [])
                   for field in BUSINESS_FIELDS}

        junk_names = {location.strip().lower(), category.strip().lower()} - {""}
        name_lower = str(profile.get("business_name", "")).strip().lower()
        looks_like_listicle = any(marker in name_lower for marker in LISTICLE_NAME_MARKERS)
        if not profile.get("business_name") or name_lower in junk_names or looks_like_listicle:
            return self._fallback_extract(html, source_url, category, location)

        profile["source_urls"] = {
            f: source_url for f in
            ["address", "phone", "email", "website", "working_hours", "rating",
             "license_information", "certifications"]
            if profile.get(f)
        }
        if profile.get("license_information"):
            profile["source_urls"]["license"] = source_url
        profile["_raw_source"] = source_url
        return profile