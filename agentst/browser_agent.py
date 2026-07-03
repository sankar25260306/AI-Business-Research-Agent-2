"""
agents/browser_agent.py
Phase 2/3: Autonomous Browser Navigation + Complete Business Discovery.

This is the core differentiator vs an "API aggregator" approach: every search
and every page visit happens through a REAL, VISIBLE Chromium browser driven
by Playwright. No search engine APIs, no scraping APIs are used here -
just normal page navigation, typing, clicking, and scrolling like a human.
"""

import asyncio
import os
import random
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Dict
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from loguru import logger
from playwright.async_api import async_playwright, Page, BrowserContext

from core.config import BROWSER, SEARCH

# Engines we drive directly through their normal search UI (not their APIs)
ENGINE_URLS = {
    "google": "https://www.google.com",
    "bing": "https://www.bing.com",
    "duckduckgo": "https://duckduckgo.com",
}

ENGINE_SEARCH_URLS = {
    "google": "https://www.google.com/search?q={query}",
    "bing": "https://www.bing.com/search?q={query}",
    "duckduckgo": "https://duckduckgo.com/?q={query}",
}


class BrowserAgent:
    """
    Owns one visible browser instance for the whole research session.
    Other agents call into this to get URLs / discover businesses;
    the ExtractionAgent later opens those URLs again (also via this agent)
    to pull page text for the LLM to read.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context: BrowserContext | None = None
        self._chromium_proc = None
        self.visited_urls: Set[str] = set()
        self.pages_crawled = 0

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def start(self):
        self._playwright = await async_playwright().start()
        try:
            Path(BROWSER.user_data_dir).mkdir(parents=True, exist_ok=True)
            self._context = await self._playwright.chromium.launch_persistent_context(
                BROWSER.user_data_dir,
                headless=BROWSER.headless,
                slow_mo=BROWSER.slow_mo_ms,
                args=[
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                user_agent=BROWSER.user_agent,
                viewport=BROWSER.viewport,
            )
            self._browser = self._context.browser
            logger.info(
                "Browser session started with persistent profile '{}' (visible={}).",
                BROWSER.user_data_dir,
                not BROWSER.headless,
            )
        except NotImplementedError:
            if not sys.platform.startswith("win"):
                raise
            logger.warning(
                "Playwright launch hit the Windows subprocess event-loop limitation; "
                "falling back to an externally launched Chromium over CDP."
            )
            try:
                await self._start_windows_cdp_fallback()
            except Exception:
                if self._chromium_proc:
                    self._chromium_proc.terminate()
                    self._chromium_proc = None
                await self._playwright.stop()
                self._playwright = None
                raise
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise

        self._context.set_default_navigation_timeout(BROWSER.nav_timeout_ms)

    async def _start_windows_cdp_fallback(self):
        chromium_path = self._find_playwright_chromium()
        if not chromium_path:
            raise RuntimeError(
                "Could not find a Playwright Chromium executable. Run `playwright install chromium`."
            )
        port = self._free_port()
        Path(BROWSER.user_data_dir).mkdir(parents=True, exist_ok=True)
        user_data_dir = os.path.abspath(BROWSER.user_data_dir)
        self._chromium_proc = subprocess.Popen([
            chromium_path,
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={user_data_dir}",
        ])
        await asyncio.sleep(2)
        self._browser = await self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context(
            user_agent=BROWSER.user_agent,
            viewport=BROWSER.viewport,
        )
        logger.info("Browser session started via CDP fallback (visible=True).")

    def _find_playwright_chromium(self) -> str | None:
        browser_root = os.path.expanduser(r"~\AppData\Local\ms-playwright")
        if not os.path.isdir(browser_root):
            return None
        candidates = []
        for root, _, files in os.walk(browser_root):
            if "chrome.exe" in files and "chrome-win" in root:
                candidates.append(os.path.join(root, "chrome.exe"))
        return sorted(candidates, reverse=True)[0] if candidates else None

    def _free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    async def stop(self):
        if self._context:
            await self._context.close()
        if self._browser and not self._context:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        if hasattr(self, "_chromium_proc") and self._chromium_proc:
            self._chromium_proc.terminate()
        logger.info("Browser session closed.")

    async def new_page(self) -> Page:
        page = await self._context.new_page()
        return page

    # ------------------------------------------------------------------ #
    # search engine driving (typing into the real search box, not an API)
    # ------------------------------------------------------------------ #
    async def search_engine(self, engine: str, query: str, max_results: int = 20) -> List[Dict]:
        page = await self.new_page()
        results: List[Dict] = []
        try:
            typed_search = await self._search_by_typing(page, engine, query)
            if not typed_search:
                await self._search_by_url(page, engine, query)

            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                logger.debug("Search results page stayed network-active; continuing with DOM extraction.")
            captcha_blocked = await self._maybe_handle_captcha(page)
            if captcha_blocked:
                logger.warning("Skipping blocked search page for engine={} query='{}'.", engine, query)
                return []

            results = await self._scrape_serp_with_pagination(page, engine, max_results)
        except Exception as e:
            logger.warning(f"search_engine({engine}, '{query}') failed: {e}")
        finally:
            await page.close()
        return results

    async def _search_by_typing(self, page: Page, engine: str, query: str) -> bool:
        try:
            await page.goto(ENGINE_URLS[engine], wait_until="domcontentloaded", timeout=BROWSER.nav_timeout_ms)
            if await self._maybe_handle_captcha(page):
                logger.warning("Search homepage blocked for engine={}.", engine)
                return False
            await self._maybe_dismiss_consent(page)

            box_selector = {
                "google": 'textarea[name="q"], input[name="q"]',
                "bing": 'input[name="q"]',
                "duckduckgo": 'input[name="q"], input#searchbox_input',
            }[engine]

            await page.wait_for_selector(box_selector, timeout=10000)
            await page.click(box_selector)
            await page.type(box_selector, query, delay=random.randint(20, 60))
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=BROWSER.nav_timeout_ms)
            return True
        except Exception as exc:
            logger.debug("Typed search failed for engine={} query='{}': {}", engine, query, exc)
            return False

    async def _search_by_url(self, page: Page, engine: str, query: str):
        url = ENGINE_SEARCH_URLS[engine].format(query=quote_plus(query))
        logger.info("Falling back to visible browser search URL for engine={} query='{}'.", engine, query)
        await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER.nav_timeout_ms)

    async def _scrape_serp_with_pagination(self, page: Page, engine: str, max_results: int) -> List[Dict]:
        """Handles 'Load more', infinite scroll, and Next-page links like a human would."""
        collected: List[Dict] = []
        link_selector = {
            "google": "div#search a:has(h3)",
            "bing": "li.b_algo h2 a",
            "duckduckgo": "a[data-testid='result-title-a']",
        }[engine]

        rounds = 0
        while len(collected) < max_results and rounds < 5:
            # natural scrolling - triggers lazy-loaded results on some engines
            await page.mouse.wheel(0, 1800)
            await asyncio.sleep(random.uniform(0.6, 1.2))

            collected = self._merge_results(
                collected,
                await self._extract_result_links(page, link_selector),
            )

            # try 'Next' / 'More results' style controls
            next_clicked = await self._click_next_or_more(page)
            if not next_clicked:
                break
            rounds += 1
            await page.wait_for_load_state("domcontentloaded")
            if await self._maybe_handle_captcha(page):
                break

        return collected[:max_results]

    async def _extract_result_links(self, page: Page, preferred_selector: str) -> List[Dict]:
        links = await self._links_from_selector(page, preferred_selector)
        if len(links) >= 3:
            return links

        fallback_links = []
        for _ in range(3):
            try:
                fallback_links = await page.evaluate(
                    """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        url: a.href,
                        title: (a.innerText || a.getAttribute('aria-label') || a.textContent || '').trim()
                    }))"""
                )
                break
            except Exception as exc:
                logger.debug("Link extraction retried while page was changing: {}", exc)
                await asyncio.sleep(0.7)
        return self._filter_search_links(fallback_links)

    async def _links_from_selector(self, page: Page, selector: str) -> List[Dict]:
        anchors = await page.query_selector_all(selector)
        links = []
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            title = (await anchor.inner_text()) or ""
            links.append({"url": href or "", "title": title.strip()})
        return self._filter_search_links(links)

    def _filter_search_links(self, links: List[Dict]) -> List[Dict]:
        filtered = []
        seen = set()
        for link in links:
            url = self._unwrap_search_url(link.get("url", ""))
            title = (link.get("title") or "").strip()
            if not url or url in seen or not self._is_public_result_url(url):
                continue
            seen.add(url)
            filtered.append({"url": url, "title": title or urlparse(url).netloc})
        return filtered

    def _merge_results(self, existing: List[Dict], incoming: List[Dict]) -> List[Dict]:
        seen = {item["url"] for item in existing}
        merged = list(existing)
        for item in incoming:
            if item["url"] not in seen:
                merged.append(item)
                seen.add(item["url"])
        return merged

    def _unwrap_search_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        for key in ("url", "u", "uddg", "q"):
            value = qs.get(key, [""])[0]
            if value.startswith(("http://", "https://")):
                return unquote(value)
        return url

    def _is_public_result_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        domain = parsed.netloc.replace("www.", "").lower()
        blocked_domains = {
            "google.com", "bing.com", "duckduckgo.com", "microsoft.com",
            "accounts.google.com", "support.google.com", "policies.google.com",
        }
        if any(domain == blocked or domain.endswith("." + blocked) for blocked in blocked_domains):
            return False
        blocked_paths = ("/search", "/preferences", "/settings", "/maps")
        return not any(parsed.path.startswith(path) for path in blocked_paths)

    async def _click_next_or_more(self, page: Page) -> bool:
        candidates = [
            "text=/^Next$/i", "a#pnnext", "text=/More results/i",
            "text=/Load more/i", "button:has-text('More')",
        ]
        for sel in candidates:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click()
                    return True
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------ #
    # CAPTCHA + consent banner handling
    # ------------------------------------------------------------------ #
    async def _maybe_handle_captcha(self, page: Page) -> bool:
        visible_text = await self._visible_text(page)
        url = page.url.lower()
        blocked = (
            "/sorry/" in url
            or "unusual traffic" in visible_text
            or "verify you are human" in visible_text
            or "our systems have detected unusual traffic" in visible_text
            or ("captcha" in visible_text and "enter the characters" in visible_text)
        )
        if blocked:
            logger.warning(
                "CAPTCHA detected on {} - pausing for manual solve (visible browser). "
                "Solve it in the Chromium window. Waiting up to {} seconds for it to clear...",
                page.url,
                BROWSER.captcha_timeout_ms // 1000,
            )
            try:
                await page.wait_for_function(
                    """() => {
                        const text = document.body.innerText.toLowerCase();
                        return !location.href.includes('/sorry/') &&
                            !text.includes('unusual traffic') &&
                            !text.includes('verify you are human') &&
                            !text.includes('our systems have detected unusual traffic');
                    }""",
                    timeout=BROWSER.captcha_timeout_ms,
                )
                return False
            except Exception:
                logger.warning("CAPTCHA did not clear in time; skipping this query.")
                return True
        return False

    async def _visible_text(self, page: Page) -> str:
        for _ in range(3):
            try:
                return ((await page.locator("body").inner_text(timeout=3000)) or "").lower()
            except Exception as exc:
                logger.debug("Visible text read retried while page was changing: {}", exc)
                await asyncio.sleep(0.7)
        return ""

    async def _maybe_dismiss_consent(self, page: Page):
        for text in ["Accept all", "I agree", "Accept", "Reject all"]:
            try:
                btn = await page.query_selector(f"text='{text}'")
                if btn and await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.3)
                    return
            except Exception:
                continue

    # ------------------------------------------------------------------ #
    # visiting individual business / directory pages
    # ------------------------------------------------------------------ #
    async def fetch_page_html(self, url: str) -> str | None:
        if url in self.visited_urls:
            return None
        domain = urlparse(url).netloc
        if not domain:
            return None

        page = await self.new_page()
        html = None
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=BROWSER.nav_timeout_ms)
            if await self._maybe_handle_captcha(page):
                logger.warning("Skipping blocked page fetch for {}.", url)
                return None

            await self._maybe_dismiss_consent(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=BROWSER.nav_timeout_ms)
            except Exception:
                logger.debug("Page did not reach networkidle before timeout: %s", url)

            # scroll a bit so lazy-loaded content (hours, reviews) renders
            for _ in range(3):
                await page.mouse.wheel(0, 1200)
                await asyncio.sleep(0.4)

            html = await page.content()
            if html and len(html) > 0:
                self.visited_urls.add(url)
                self.pages_crawled += 1
                logger.debug("Fetched page HTML for %s (size=%d).", url, len(html))
        except Exception as e:
            logger.debug(f"fetch_page_html failed for {url}: {e}")
        finally:
            await page.close()
        return html
