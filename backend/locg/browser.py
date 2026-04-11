"""
LoCG Browser — uses Playwright to navigate League of Comic Geeks and collect data.

Approach:
- Drives a real headless Chromium browser (no undocumented API assumptions)
- Navigates series pages and issue pages just as a user would
- Intercepts XHR/fetch network responses as pages load
- Falls back to HTML scraping if network interception yields no useful data
- Rate limits: 2 second delay between page navigations

URL patterns discovered by browsing LoCG (documented in endpoints.md):
  Series page:  https://leagueofcomicgeeks.com/comics/series/{series_id}/{slug}
  Issue page:   https://leagueofcomicgeeks.com/comics/{issue_id}/{slug}
  Search:       POST https://leagueofcomicgeeks.com/comic/search
  New releases: https://leagueofcomicgeeks.com/comics/new-releases/{YYYY-MM-DD}
"""

import asyncio
import json
import os
import re
import logging
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse, urlencode, quote_plus

from playwright.async_api import async_playwright, Page, Response, BrowserContext

logger = logging.getLogger(__name__)

BASE_URL = "https://leagueofcomicgeeks.com"

# Realistic Chrome User-Agent matching what their own frontend sends
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

NAV_DELAY = 2.0  # seconds between page navigations (politeness)


# ---------------------------------------------------------------------------
# Browser context factory
# ---------------------------------------------------------------------------

async def _make_browser_context(playwright) -> tuple:
    """Launch headless Chromium and return (browser, context)."""
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="America/New_York",
        extra_http_headers={
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "DNT": "1",
        },
    )
    # Hide webdriver fingerprint
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return browser, context


# ---------------------------------------------------------------------------
# LoCG authentication
# ---------------------------------------------------------------------------

async def _login_to_locg(page: Page) -> bool:
    """
    Log in to LoCG using LOCG_USERNAME / LOCG_PASSWORD.
    Reads from os.environ first, then falls back to the Pydantic settings object
    (which reads from the .env file via BaseSettings).
    Returns True if login succeeded, False if credentials missing or login failed.
    Credentials are never hardcoded.
    """
    username = os.environ.get("LOCG_USERNAME", "").strip()
    password = os.environ.get("LOCG_PASSWORD", "").strip()
    # Pydantic's BaseSettings reads .env into settings fields but does NOT inject
    # them into os.environ — fall back to settings object when env vars are absent.
    if not username or not password:
        try:
            from backend.config import settings as _settings
            username = username or (_settings.locg_username or "").strip()
            password = password or (_settings.locg_password or "").strip()
        except Exception:
            pass
    if not username or not password:
        logger.info("LOCG credentials not set — skipping login")
        return False

    logger.info("Logging into LoCG as %s", username)
    try:
        await page.goto(
            f"{BASE_URL}/login",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        await asyncio.sleep(1)
        await page.fill('input[name="username"]', username)
        await page.fill('input[name="password"]', password)
        await page.click('#submit')
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)

        # Confirm login: logged-in pages show a logout link or user nav
        logged_in = await page.evaluate(
            "() => !!document.querySelector('a[href*=\"/logout\"], a[href*=\"/profile/\"], .user-nav, #user-menu')"
        )
        if logged_in:
            logger.info("LoCG login successful")
        else:
            logger.warning("LoCG login may have failed (no logged-in indicator found)")
        return logged_in
    except Exception as exc:
        logger.warning("LoCG login error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Network interception helpers
# ---------------------------------------------------------------------------

def _make_json_interceptor(captured: list, url_patterns: list[str]):
    """
    Return an async handler that captures JSON responses whose URLs match any
    of the given substring patterns.
    """
    async def handler(response: Response):
        try:
            url = response.url
            if not any(pat in url for pat in url_patterns):
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                return
            if response.status < 200 or response.status >= 300:
                return
            body = await response.body()
            text = body.decode("utf-8", errors="replace").strip()
            # Must start with { or [ to be JSON
            if text and text[0] in ("{", "["):
                try:
                    data = json.loads(text)
                    captured.append({"url": url, "data": data})
                    logger.debug("Captured JSON from %s", url)
                except json.JSONDecodeError:
                    pass
        except Exception as exc:
            logger.debug("Interceptor error: %s", exc)

    return handler


# ---------------------------------------------------------------------------
# HTML scraping helpers
# ---------------------------------------------------------------------------

async def _page_text(page: Page) -> str:
    """Return full page HTML as text."""
    return await page.content()


async def _scrape_series_issues_from_html(page: Page, series_url: str) -> list[dict]:
    """
    Scrape issue listing from a LoCG series page HTML.

    LoCG actual DOM structure (discovered via Playwright inspection):
        <li class="issue" data-comic="{locg_issue_id}" data-pulls="...">
          <a href="/comic/{locg_issue_id}/{slug}">
            <img alt="{series name} #{issue_number}" src="...cover.jpg">
          </a>
        </li>

    Note: issue URLs use /comic/ (singular), series URLs use /comics/series/ (plural).
    """
    issues = await page.evaluate("""
        () => {
            const results = [];

            // LoCG renders each issue as li.issue[data-comic] after the
            // comic/get_comics XHR resolves and inserts HTML into the DOM.
            const items = document.querySelectorAll('li.issue[data-comic]');

            items.forEach(item => {
                try {
                    const obj = {};

                    // Issue ID from data-comic attribute (e.g. data-comic="6512949")
                    // Real LoCG comic IDs are large integers (6-7+ digits).
                    // Small values (< 100000) are navigation buttons or UI placeholders.
                    const dataComic = item.getAttribute('data-comic');
                    if (dataComic && /^\\d+$/.test(dataComic) && parseInt(dataComic) > 100000) {
                        obj.locg_issue_id = dataComic;
                    } else {
                        return; // skip nav pseudo-items (data-comic="-7") and UI placeholders
                    }

                    // Issue URL — links use /comic/ (singular)
                    // Require a valid /comic/ link — items without one are UI elements,
                    // not navigable issues we can fetch detail for.
                    // Skip variant cover items (separate li.issue entries with ?variant= URLs)
                    // Cover variants are fetched via get_issue_detail instead
                    const link = item.querySelector('a[href*="/comic/"]');
                    if (!link || link.href.includes('?variant=')) return;
                    obj.issue_url = link.href;

                    // Issue number — extracted from img alt text like "absolute batman #21"
                    const img = item.querySelector('img');
                    if (img) {
                        obj.cover_image_url = img.src
                            || img.dataset.src
                            || img.getAttribute('data-src')
                            || null;
                        if (img.alt) {
                            // Title is the full alt text
                            obj.title = img.alt;
                            // Issue number: match "#21" or "#21A" pattern
                            const numMatch = img.alt.match(/#(\\d+[A-Za-z]*)/i);
                            if (numMatch) obj.issue_number = numMatch[1];
                        }
                    }

                    // Release date — check for data attribute on the <li>
                    const dateAttr = item.getAttribute('data-release-date')
                        || item.dataset.releaseDate
                        || item.dataset.date;
                    if (dateAttr) obj.release_date_raw = dateAttr;

                    // Date from text node inside item
                    if (!obj.release_date_raw) {
                        const dateEl = item.querySelector('.date, .release-date, time, [class*="date"]');
                        if (dateEl) obj.release_date_raw = dateEl.textContent.trim();
                    }

                    results.push(obj);
                } catch (e) {}
            });

            return results;
        }
    """)
    return issues or []


async def _scrape_issue_detail_from_html(page: Page) -> dict:
    """
    Scrape full issue detail from a LoCG issue page HTML using Playwright evaluate.

    LoCG actual DOM structure (discovered via Playwright inspection):
    - Issue ID:   window.location.pathname matches /comic/{id}/
    - FOC date:   .details-addtl-block where .name == "Final Order Cutoff", value in .value
                  (value contains Material Icons text "calendar_month" that must be stripped)
                  Date format: "May 18th" (year-less — parsers.py infers year)
    - Release:    Header text "DC COMICS · RELEASES JUN 10, 2026" → match "RELEASES ..."
    - Variants:   .variant-cover-list a[href*="?variant="]
                  - cover label: title / data-original-title attribute on <a>
                  - cover image: img[data-src] (lazy loaded)
                  - variant ID:  ?variant={id} in href
    - Series:     a[href*="/comics/series/"] (series URLs are /comics/series/ plural)
    """
    detail = await page.evaluate("""
        () => {
            const result = {};

            // --- Issue ID from URL ---
            // Issue pages use /comic/{id}/ (singular); series pages use /comics/series/ (plural)
            const urlMatch = window.location.pathname.match(/\\/comic\\/(\\d+)/);
            if (urlMatch) result.locg_issue_id = urlMatch[1];
            result.issue_url = window.location.href;

            // --- Title and issue number ---
            const h1 = document.querySelector('h1.comic-title, h1, .comic-detail h1, .title h1');
            if (h1) {
                result.full_title = h1.textContent.trim();
                const numMatch = h1.textContent.match(/#(\\d+[A-Za-z]*)/);
                if (numMatch) result.issue_number = numMatch[1];
            }

            // --- Release date from header text ---
            // Header has text like "DC COMICS · RELEASES JUN 10, 2026"
            const allText = document.body.innerText;
            const releasesMatch = allText.match(/RELEASES\\s+([A-Za-z]+\\.?\\s+\\d{1,2},?\\s+\\d{4})/i);
            if (releasesMatch) result.release_date_raw = releasesMatch[1].trim();

            // Fallback: structured date elements
            if (!result.release_date_raw) {
                const dateEl = document.querySelector(
                    '.release-date, [class*="release-date"], time[datetime]'
                );
                if (dateEl) {
                    result.release_date_raw = dateEl.getAttribute('datetime')
                        || dateEl.textContent.trim();
                }
            }

            // --- FOC date and Cover Date from .details-addtl-block ---
            // Each block has: <div class="name">Label</div><div class="value">Value</div>
            // FOC value contains a Material Icons span with text "calendar_month" that we strip
            document.querySelectorAll('.details-addtl-block').forEach(block => {
                const nameEl = block.querySelector('.name');
                const valueEl = block.querySelector('.value');
                if (!nameEl || !valueEl) return;
                const nameLower = nameEl.textContent.trim().toLowerCase();

                // Clone and strip Material Icons spans to get clean text
                const valueClone = valueEl.cloneNode(true);
                valueClone.querySelectorAll('.material-icons, [class*="material-icons"]')
                    .forEach(s => s.remove());
                const valueText = valueClone.textContent.trim();

                if (nameLower.includes('final order')) {
                    result.foc_date_raw = valueText;
                } else if (nameLower === 'cover date') {
                    result.cover_date_raw = valueText; // e.g. "Aug 2026" (not used for FOC)
                }
            });

            // --- Reprint detection ---
            const titleText = (result.full_title || '').toLowerCase();
            const bodyText = allText.toLowerCase();
            result.is_reprint = (
                titleText.includes('2nd print') ||
                titleText.includes('second print') ||
                titleText.includes('reprint') ||
                titleText.includes('facsimile') ||
                bodyText.includes('2nd printing') ||
                bodyText.includes('second printing')
            );

            // --- Primary cover image from .cover-gallery ---
            const primaryImg = document.querySelector('.cover-gallery img');
            if (primaryImg && primaryImg.src && !primaryImg.src.startsWith('data:')) {
                result.cover_image_url = primaryImg.src;
            }

            // --- Cover variants from .variant-cover-list ---
            // Each variant: <a href="...?variant={id}" title="Cover B Artist Name">
            //                   <img data-src="https://...cover.jpg">
            //               </a>
            result.covers = [];
            const variantList = document.querySelector('.variant-cover-list');
            if (variantList) {
                variantList.querySelectorAll('a[href*="?variant="]').forEach(link => {
                    try {
                        const cover = {};

                        // Variant ID from URL
                        const variantMatch = link.href.match(/\\?variant=(\\d+)/);
                        if (variantMatch) cover.locg_cover_id = variantMatch[1];

                        // Cover label from title or data-original-title attribute
                        const titleAttr = (
                            link.getAttribute('title') ||
                            link.getAttribute('data-original-title') || ''
                        ).trim();
                        if (titleAttr) cover.cover_label = titleAttr;

                        // Cover image from data-src (lazy loaded — src is 1×1 placeholder)
                        const img = link.querySelector('img');
                        if (img) {
                            const dataSrc = img.getAttribute('data-src');
                            if (dataSrc && !dataSrc.startsWith('data:')) {
                                cover.cover_image_url = dataSrc;
                            }
                        }

                        cover.artists = [];

                        if (cover.locg_cover_id || cover.cover_label) {
                            result.covers.push(cover);
                        }
                    } catch (e) {}
                });
            }

            // --- Series info ---
            // Skip "Submit New Variant Cover" / submit-new-issue links that appear before the real series link.
            // A valid series link points to /comics/series/{id}/{slug} with no query params.
            let seriesLink = null;
            document.querySelectorAll('a[href*="/comics/series/"]').forEach(a => {
                if (seriesLink) return;
                const href = a.href;
                if (href.includes('submit-new-issue') || href.includes('?') || href.includes('/submit')) return;
                const txt = a.textContent.trim();
                if (!txt || txt.toLowerCase() === 'series' || txt.toLowerCase() === 'add') return;
                seriesLink = a;
            });
            if (seriesLink) {
                // Strip trailing year range like " (2025 - Present)" from series name
                result.series_name = seriesLink.textContent.trim().replace(/\s*\(\d{4}[^)]*\)\s*$/, '').trim();
                result.series_url = seriesLink.href.split('?')[0];
                const sMatch = seriesLink.href.match(/\\/comics\\/series\\/(\\d+)/);
                if (sMatch) result.locg_series_id = sMatch[1];
            }

            // --- Publisher ---
            const pubEl = document.querySelector(
                '.publisher, [class*="publisher"], a[href*="/publisher/"]'
            );
            if (pubEl) result.publisher = pubEl.textContent.trim();

            // --- Creator credits ---
            // LoCG stores full creator credits in a separate contributions page.
            // The issue overview tab doesn't expose creator data in a structured way,
            // so we return an empty list. Artist-cover linkage is inferred from
            // cover labels (which include artist names) during alert jobs.
            result.creators = [];

            return result;
        }
    """)
    return detail or {}


async def _scrape_search_results_from_html(page: Page) -> list[dict]:
    """Scrape search results from a LoCG search page."""
    results = await page.evaluate("""
        () => {
            const items = [];
            const selectors = [
                '.comic-item',
                '.series-item',
                '.search-result',
                '[class*="search-result"]',
                '.comic-list li',
            ];
            const els = document.querySelectorAll(selectors.join(', '));
            els.forEach(el => {
                try {
                    const obj = {};

                    // Link to series or issue page
                    const link = el.querySelector('a[href*="/comics/"]');
                    if (link) {
                        obj.url = link.href;
                        // Detect series vs issue
                        if (link.href.includes('/series/')) {
                            obj.type = 'series';
                            const m = link.href.match(/\\/comics\\/series\\/(\\d+)/);
                            if (m) obj.locg_series_id = m[1];
                            // Extract slug
                            const parts = link.href.split('/');
                            obj.slug = parts[parts.length - 1] || parts[parts.length - 2];
                        } else {
                            obj.type = 'comic';
                            const m = link.href.match(/\\/comics\\/(\\d+)/);
                            if (m) obj.locg_issue_id = m[1];
                        }
                    }

                    // Name/title
                    const nameEl = el.querySelector('h3, h4, .title, .name, .comic-title');
                    if (nameEl) obj.name = nameEl.textContent.trim();

                    // Publisher
                    const pubEl = el.querySelector('.publisher, [class*="publisher"]');
                    if (pubEl) obj.publisher = pubEl.textContent.trim();

                    // Cover image
                    const img = el.querySelector('img');
                    if (img) obj.cover_image_url = img.src || img.dataset.src;

                    if (obj.url || obj.name) items.push(obj);
                } catch (e) {}
            });
            return items;
        }
    """)
    return results or []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def search_series(query: str) -> list[dict]:
    """
    Search LoCG for a series by name and return results.

    First tries the XHR search endpoint that the site's autocomplete uses.
    Falls back to loading the HTML search results page.

    Returns list of dicts with keys:
        name, locg_series_id, slug, publisher, cover_image_url, url
    """
    results = []
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()
            captured_json: list[dict] = []

            # Intercept XHR/fetch JSON responses
            page.on(
                "response",
                _make_json_interceptor(
                    captured_json,
                    ["/comic/search", "/comics/search", "/search"],
                ),
            )

            # Navigate to the search page
            search_url = f"{BASE_URL}/comics/search?query={quote_plus(query)}"
            logger.info("Searching LoCG: %s", search_url)
            try:
                await page.goto(search_url, wait_until="networkidle", timeout=30_000)
            except Exception as exc:
                logger.warning("networkidle timeout on search, trying domcontentloaded: %s", exc)
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20_000)

            await asyncio.sleep(NAV_DELAY)

            # Check if we got any JSON via interception
            for capture in captured_json:
                data = capture["data"]
                if isinstance(data, list) and data:
                    logger.info("Got %d results from JSON interception", len(data))
                    return data  # Return raw; parsers.py normalizes
                if isinstance(data, dict) and ("results" in data or "data" in data):
                    inner = data.get("results") or data.get("data", [])
                    if inner:
                        return inner

            # Fallback: scrape HTML
            logger.info("Falling back to HTML scraping for search results")
            results = await _scrape_search_results_from_html(page)

            # If HTML scraping also failed, try a direct XHR approach via fetch inside the page
            if not results:
                logger.info("Trying fetch() from inside page context for search")
                try:
                    xhr_result = await page.evaluate(f"""
                        async () => {{
                            try {{
                                const resp = await fetch(
                                    '/comic/search',
                                    {{
                                        method: 'POST',
                                        headers: {{
                                            'Content-Type': 'application/x-www-form-urlencoded',
                                            'X-Requested-With': 'XMLHttpRequest',
                                        }},
                                        body: 'query={quote_plus(query)}&type=series',
                                    }}
                                );
                                const text = await resp.text();
                                return text;
                            }} catch (e) {{
                                return null;
                            }}
                        }}
                    """)
                    if xhr_result:
                        try:
                            parsed = json.loads(xhr_result)
                            if isinstance(parsed, list):
                                results = parsed
                            elif isinstance(parsed, dict):
                                results = parsed.get("results") or parsed.get("data") or []
                        except json.JSONDecodeError:
                            pass
                except Exception as exc:
                    logger.warning("In-page fetch for search failed: %s", exc)

        finally:
            await context.close()
            await browser.close()

    return results


async def get_series_issues(series_url: str) -> list[dict]:
    """
    Navigate to a LoCG series page and return list of issues with basic info.

    series_url: full URL like https://leagueofcomicgeeks.com/comics/series/12345/slug

    Returns list of dicts with keys:
        locg_issue_id, issue_number, title, release_date_raw,
        cover_image_url, issue_url
    """
    issues: list[dict] = []
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()
            captured_json: list[dict] = []

            page.on(
                "response",
                _make_json_interceptor(
                    captured_json,
                    # comic/get_comics?series_id= returns {"list":"<html>"} — captured
                    # but not used directly since DOM scraping is more reliable
                    ["/get_comics", "series_id=", "/issues"],
                ),
            )

            logger.info("Fetching series page: %s", series_url)
            try:
                await page.goto(series_url, wait_until="networkidle", timeout=30_000)
            except Exception as exc:
                logger.warning("networkidle timeout on series page: %s", exc)
                await page.goto(series_url, wait_until="domcontentloaded", timeout=20_000)

            await asyncio.sleep(NAV_DELAY)

            # Try JSON interception first
            for capture in captured_json:
                data = capture["data"]
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    if any(k in data[0] for k in ("issue_number", "number", "comic_id", "id")):
                        logger.info("Got %d issues from JSON interception", len(data))
                        return data
                if isinstance(data, dict):
                    for key in ("issues", "data", "comics", "results"):
                        if key in data and isinstance(data[key], list) and data[key]:
                            logger.info("Got %d issues from JSON interception (key=%s)", len(data[key]), key)
                            return data[key]

            # Fallback: HTML scraping
            logger.info("Falling back to HTML scraping for series issues")
            issues = await _scrape_series_issues_from_html(page, series_url)

            # If still empty, try scrolling/clicking to load more
            if not issues:
                logger.info("No issues found yet, trying scroll/load-more")
                try:
                    # Scroll to bottom to trigger lazy loading
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1.5)
                    issues = await _scrape_series_issues_from_html(page, series_url)
                except Exception as exc:
                    logger.warning("Scroll attempt failed: %s", exc)

            # Try clicking a "Load More" or "Show All" button
            if not issues:
                try:
                    load_more = page.locator(
                        'button:has-text("Load More"), a:has-text("Show All"), '
                        '[class*="load-more"], [class*="show-all"]'
                    )
                    if await load_more.count() > 0:
                        await load_more.first.click()
                        await asyncio.sleep(2)
                        issues = await _scrape_series_issues_from_html(page, series_url)
                except Exception as exc:
                    logger.warning("Load-more click failed: %s", exc)

            # Last resort: re-try li.issue selector with a broader fallback
            if not issues:
                logger.info("Last resort: re-querying li[data-comic] and /comic/ links from page")
                issues = await page.evaluate("""
                    () => {
                        const results = [];
                        const seen = new Set();

                        // Try any element with data-comic (positive integer only)
                        document.querySelectorAll('[data-comic]').forEach(el => {
                            const dc = el.getAttribute('data-comic');
                            if (!dc || !/^\\d+$/.test(dc) || seen.has(dc)) return;
                            seen.add(dc);
                            const link = el.querySelector('a[href*="/comic/"]') || el.closest('a');
                            const img = el.querySelector('img');
                            const obj = { locg_issue_id: dc };
                            if (link) obj.issue_url = link.href;
                            if (img) {
                                obj.cover_image_url = img.src || null;
                                if (img.alt) {
                                    obj.title = img.alt;
                                    const m = img.alt.match(/#(\\d+[A-Za-z]*)/i);
                                    if (m) obj.issue_number = m[1];
                                }
                            }
                            results.push(obj);
                        });

                        // Fallback: /comic/{numeric_id}/ links (singular, not /comics/series/)
                        if (results.length === 0) {
                            document.querySelectorAll('a[href*="/comic/"]').forEach(a => {
                                const href = a.href;
                                if (href.includes('/series/') || href.includes('/search')) return;
                                const m = href.match(/\\/comic\\/(\\d+)/);
                                if (!m || seen.has(m[1])) return;
                                seen.add(m[1]);
                                results.push({
                                    issue_url: href,
                                    locg_issue_id: m[1],
                                    title: a.textContent.trim() || null,
                                });
                            });
                        }

                        return results;
                    }
                """)

        finally:
            await context.close()
            await browser.close()

    logger.info("Returning %d issues for %s", len(issues), series_url)
    return issues


def _normalize_profile_url(artist_url: str) -> str:
    """Return the base LoCG creator profile URL, stripping sub-paths like /comics."""
    m = re.match(r'(https://leagueofcomicgeeks\.com/people/\d+/[^/?#]+)', artist_url)
    return m.group(1) if m else artist_url


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_locg_date(text: str) -> Optional[date]:
    """Parse LoCG date strings like 'MAY 27TH, 2026' or 'APR 15' into a date."""
    if not text:
        return None
    # Full format: "MAY 27TH, 2026"
    m = re.search(
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})',
        text, re.I,
    )
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        day, year = int(m.group(2)), int(m.group(3))
        try:
            return date(year, month, day)
        except ValueError:
            return None
    # Short format: "APR 15" (no year — infer year)
    m = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})', text, re.I)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower())
        day = int(m.group(2))
        today = date.today()
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
                if candidate >= today:
                    return candidate
            except ValueError:
                pass
    return None


async def get_artist_upcoming_issues(artist_url: str) -> list[dict]:
    """
    Return upcoming issues (within 12 weeks) for a tracked artist.

    Strategy:
    - Extract creator ID from the profile URL (the numeric segment after /people/).
    - Log in, navigate to /comics page to establish session cookies, then call
      LoCG's internal get_comics API directly with a date range filter.
      This matches exactly what the UI shows when you open the page and filter
      by release date (from today to 12 weeks out).
    - Falls back to the public profile page #creator-upcoming section if not
      logged in or if login fails.

    Returns list of dicts:
        locg_issue_id, issue_url, title, issue_number, cover_image_url
    """
    # Extract creator ID from URL: .../people/{id}/... or .../people/{id}
    creator_id_match = re.search(r"/people/(\d+)", artist_url)
    if not creator_id_match:
        logger.error("Cannot extract creator ID from URL: %s", artist_url)
        return []
    creator_id = creator_id_match.group(1)

    profile_url = _normalize_profile_url(artist_url)
    today = date.today()
    cutoff = today + timedelta(weeks=12)
    from_date_str = today.strftime("%m/%d/%Y")
    to_date_str = cutoff.strftime("%m/%d/%Y")

    issues: list[dict] = []
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()

            # ── Attempt login ──
            logged_in = await _login_to_locg(page)

            if logged_in:
                # ── Navigate to artist /comics page ──
                comics_url = profile_url.rstrip("/") + "/comics"
                logger.info("Navigating to artist /comics page: %s", comics_url)
                try:
                    await page.goto(comics_url, wait_until="networkidle", timeout=30_000)
                except Exception:
                    await page.goto(comics_url, wait_until="domcontentloaded", timeout=20_000)
                await asyncio.sleep(NAV_DELAY)

                if "restricted" in (await page.title()).lower():
                    logger.warning("/comics page restricted even after login — falling back")
                    logged_in = False

            if logged_in:
                # ── Apply date filter through the UI, exactly as a user would ──
                # 1. Open filter panel
                await page.click(".show-filters")
                await asyncio.sleep(1)

                # 2. Expand the Release Date section
                await page.click("text=RELEASE DATE")
                await asyncio.sleep(1)

                # 3. Type the from-date into the first datepicker input and press Tab.
                #    We use .type() (simulated keypresses) so the datepicker widget's
                #    keydown/input listeners fire and it registers the selected date.
                from_input = page.locator('input[id^="dp"]').first
                await from_input.click()
                await page.keyboard.press("Control+a")
                await from_input.type(from_date_str)
                await page.keyboard.press("Tab")
                await asyncio.sleep(0.5)

                # 4. Type the to-date into the second datepicker input and press Tab
                #    Pressing Tab triggers the datepicker onSelect, which fires the
                #    page's own AJAX reload — no API call from us.
                to_input = page.locator('input[id^="dp"]').nth(1)
                await to_input.click()
                await page.keyboard.press("Control+a")
                await to_input.type(to_date_str)
                await page.keyboard.press("Tab")

                # Log the actual input values so we can verify the filter was applied
                from_val = await from_input.get_attribute("value")
                to_val = await to_input.get_attribute("value")
                logger.info("Date filter inputs after fill: from=%r to=%r", from_val, to_val)

                # 5. Wait for the page to reload its comic list
                try:
                    await page.wait_for_load_state("networkidle", timeout=15_000)
                except Exception:
                    await asyncio.sleep(5)

                # 6. Close the filter panel
                await page.click(".show-filters")
                await asyncio.sleep(1)

                # 7. Read only the li[data-comic] elements that are actually visible.
                #    Comics with "+N" variant badges expand hidden sub-variant li elements
                #    into the DOM. Those hidden elements have offsetHeight == 0, so we
                #    exclude them here — keeping only what the user sees on screen.
                raw_items = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('li[data-comic]'))
                        .filter(li => li.offsetHeight > 0)
                        .map(li => ({
                            data_comic: li.dataset.comic,
                            data_parent: li.dataset.parent || '0',
                            href: (li.querySelector('a[href*="/comic/"]') || {}).href || '',
                            text: li.innerText || ''
                        }))
                """)

                logger.info("After date filter UI: %d items visible for %s", len(raw_items), profile_url)

                # 8. One entry per LoCG list item (one per cover), deduped by data-comic.
                #    This matches exactly what LoCG shows — if an artist has two covers
                #    on the same issue (e.g. main cover + a variant), both rows appear.
                #
                #    Comics with many variants (e.g. Batman #163 "+15") expand ALL their
                #    hidden sub-variants into the DOM as li[data-comic] elements even
                #    though the page only shows the primary row.  Those hidden elements
                #    have empty / whitespace-only innerText, so we skip them.
                seen_variant_keys: set = set()
                for item in raw_items:
                    if not item.get("text", "").strip():
                        continue  # hidden collapsed variant — not visible on the page
                    href = item.get("href", "")
                    m = re.search(r"/comic/(\d+)/([^?#]+)", href)
                    if m:
                        canonical_id = m.group(1)
                        slug = m.group(2)
                        issue_url = f"{BASE_URL}/comic/{canonical_id}/{slug}"
                    else:
                        parent = item.get("data_parent", "0")
                        canonical_id = parent if parent != "0" else item.get("data_comic", "")
                        issue_url = None

                    if not canonical_id:
                        continue

                    cover_variant_id = item.get("data_comic", "")
                    # Dedup key: the specific cover's data-comic (unique per variant).
                    # For canonical items (dp=0) data-comic == canonical_id, so they
                    # also get a unique key.
                    dedup_key = cover_variant_id or canonical_id
                    if dedup_key in seen_variant_keys:
                        continue
                    seen_variant_keys.add(dedup_key)

                    # cover_variant_ids: the specific variant ID if this isn't the
                    # canonical cover (dp != 0 or data-comic != canonical_id).
                    variant_ids = (
                        [cover_variant_id]
                        if cover_variant_id and cover_variant_id != canonical_id
                        else []
                    )

                    issues.append({
                        "locg_issue_id": canonical_id,
                        "issue_url": issue_url,
                        "cover_image_url": None,
                        "title": "",
                        "date_text": item.get("text", ""),
                        "cover_variant_ids": variant_ids,
                    })

                logger.info("%d unique issues after dedup for %s", len(issues), profile_url)

                # 9. Post-filter: drop anything clearly outside today→cutoff.
                #    This guards against the date filter UI silently failing and
                #    returning the artist's full back-catalog.
                filtered: list[dict] = []
                for issue in issues:
                    text = issue.get("date_text", "")
                    d = _parse_locg_date(text)
                    if d is not None:
                        # Parseable date — only keep if in window
                        if today <= d <= cutoff:
                            filtered.append(issue)
                    else:
                        # No parseable date — check if an obvious old year is present
                        old_year = re.search(r'\b(19\d\d|200\d|201\d|2020|2021|2022|2023)\b', text)
                        if not old_year:
                            # No old year found — keep (could be upcoming without a date shown)
                            filtered.append(issue)
                        # else: text contains an old year → skip
                if len(filtered) < len(issues):
                    logger.info(
                        "Post-filter removed %d out-of-window items (%d remain)",
                        len(issues) - len(filtered), len(filtered),
                    )
                return filtered

            # ── Fallback: public profile page ──
            logger.info("Fetching artist profile page (public): %s", profile_url)
            try:
                await page.goto(profile_url, wait_until="networkidle", timeout=30_000)
            except Exception as exc:
                logger.warning("networkidle timeout on profile page: %s", exc)
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(NAV_DELAY)

            raw_issues = await page.evaluate("""
                () => {
                    const results = [];
                    const seen = new Set();
                    const section = document.querySelector('#comics-upcoming, #creator-upcoming');
                    const container = section || document;
                    container.querySelectorAll('a[href*="/comic/"]').forEach(link => {
                        if (link.href.includes('?variant=') || link.href.includes('/series/')) return;
                        const m = link.href.match(/\\/comic\\/(\\d+)/);
                        if (!m || seen.has(m[1])) return;
                        seen.add(m[1]);
                        const card = link.closest('.card, li, article') || link.parentElement;
                        const img = card ? card.querySelector('img') : null;
                        const src = img
                            ? ((img.src && !img.src.startsWith('data:')) ? img.src
                               : (img.dataset && img.dataset.src) ? img.dataset.src
                               : null)
                            : null;
                        const obj = {
                            locg_issue_id: m[1],
                            issue_url: link.href,
                            cover_image_url: src ? (src.startsWith('http') ? src : 'https://leagueofcomicgeeks.com' + src) : null,
                            title: img?.alt || link.textContent.trim(),
                            date_text: card?.innerText || '',
                        };
                        const nm = obj.title.match(/#(\\d+[A-Za-z]*)/i);
                        if (nm) obj.issue_number = nm[1];
                        results.push(obj);
                    });
                    return results;
                }
            """)

            for raw in raw_issues:
                d = _parse_locg_date(raw.get("date_text", ""))
                if d is None or (today <= d <= cutoff):
                    issues.append(raw)

            logger.info("Profile page: %d upcoming issues for %s", len(issues), profile_url)

        finally:
            await context.close()
            await browser.close()

    return issues


async def get_issue_detail(issue_url: str) -> dict:
    """
    Navigate to a LoCG issue page and return full detail including cover variants.

    issue_url: full URL like https://leagueofcomicgeeks.com/comics/12345/absolute-batman-5

    Returns dict with keys:
        locg_issue_id, series_name, series_url, locg_series_id,
        issue_number, full_title, publisher,
        release_date_raw, foc_date_raw,
        is_reprint, cover_image_url,
        covers: [ { cover_label, cover_image_url, locg_cover_id, artists: [...] } ],
        creators: [ { name, role, locg_creator_id, locg_url } ]
    """
    detail: dict = {}
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()
            captured_json: list[dict] = []

            page.on(
                "response",
                _make_json_interceptor(
                    captured_json,
                    ["/comic/covers", "/covers", "/comic/detail"],
                ),
            )

            logger.info("Fetching issue page: %s", issue_url)
            try:
                await page.goto(issue_url, wait_until="networkidle", timeout=10_000)
            except Exception as exc:
                logger.warning("networkidle timeout on issue page: %s", exc)
                await page.goto(issue_url, wait_until="domcontentloaded", timeout=15_000)

            await asyncio.sleep(NAV_DELAY)

            # Extract issue ID from URL for cover XHR
            # Issue pages use /comic/{id}/ (singular), not /comics/
            issue_id_match = re.search(r"/comic/(\d+)", issue_url)
            issue_id = issue_id_match.group(1) if issue_id_match else None

            # Scrape HTML first (always reliable for basic fields)
            detail = await _scrape_issue_detail_from_html(page)

            # Check JSON interception for cover data
            covers_from_json = []
            for capture in captured_json:
                data = capture["data"]
                if isinstance(data, dict) and "covers" in data:
                    covers_from_json = data["covers"]
                    logger.info("Got %d covers from JSON interception", len(covers_from_json))
                    break
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    if "cover_label" in data[0] or "label" in data[0]:
                        covers_from_json = data
                        break

            if covers_from_json:
                detail["covers"] = covers_from_json

            # If no covers yet, try fetching the covers XHR endpoint directly from page context
            if not detail.get("covers") and issue_id:
                logger.info("Trying in-page fetch for covers XHR, issue_id=%s", issue_id)
                try:
                    cover_json_str = await page.evaluate(f"""
                        async () => {{
                            try {{
                                const resp = await fetch(
                                    '/comic/covers/{issue_id}',
                                    {{
                                        headers: {{
                                            'X-Requested-With': 'XMLHttpRequest',
                                            'Accept': 'application/json',
                                        }}
                                    }}
                                );
                                return await resp.text();
                            }} catch (e) {{
                                return null;
                            }}
                        }}
                    """)
                    if cover_json_str:
                        cover_data = json.loads(cover_json_str)
                        if isinstance(cover_data, dict) and "covers" in cover_data:
                            detail["covers"] = cover_data["covers"]
                        elif isinstance(cover_data, list):
                            detail["covers"] = cover_data
                except Exception as exc:
                    logger.warning("Cover XHR fetch failed: %s", exc)

            detail["issue_url"] = issue_url

        finally:
            await context.close()
            await browser.close()

    return detail


async def get_new_releases(date_str: Optional[str] = None) -> list[dict]:
    """
    Fetch the LoCG new releases page for a given week.

    date_str: ISO date string like "2025-01-22". If None, uses current week.

    Returns list of issue dicts with basic info.
    """
    if date_str:
        url = f"{BASE_URL}/comics/new-releases/{date_str}"
    else:
        url = f"{BASE_URL}/comics/new-releases"

    releases: list[dict] = []
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()
            captured_json: list[dict] = []

            page.on(
                "response",
                _make_json_interceptor(
                    captured_json,
                    ["/new-releases", "/releases", "/comics"],
                ),
            )

            logger.info("Fetching new releases: %s", url)
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception as exc:
                logger.warning("networkidle timeout on releases page: %s", exc)
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)

            await asyncio.sleep(NAV_DELAY)

            # Try JSON interception
            for capture in captured_json:
                data = capture["data"]
                if isinstance(data, list) and len(data) > 0:
                    releases = data
                    break
                if isinstance(data, dict):
                    for key in ("releases", "comics", "data", "results"):
                        if key in data and isinstance(data[key], list):
                            releases = data[key]
                            break
                if releases:
                    break

            if not releases:
                # Fallback: scrape HTML
                releases = await page.evaluate("""
                    () => {
                        const results = [];
                        const items = document.querySelectorAll(
                            '.comic-item, .release-item, li[data-comic-id], .comics-list li'
                        );
                        items.forEach(item => {
                            try {
                                const obj = {};
                                const link = item.querySelector('a[href*="/comics/"]');
                                if (link) {
                                    obj.issue_url = link.href;
                                    const m = link.href.match(/\\/comics\\/(\\d+)/);
                                    if (m) obj.locg_issue_id = m[1];
                                }
                                const titleEl = item.querySelector('h3, h4, .title, .name');
                                if (titleEl) obj.title = titleEl.textContent.trim();
                                const img = item.querySelector('img');
                                if (img) obj.cover_image_url = img.src || img.dataset.src;
                                const dateEl = item.querySelector('.date, time, [class*="date"]');
                                if (dateEl) obj.release_date_raw = dateEl.textContent.trim();
                                if (obj.issue_url || obj.title) results.push(obj);
                            } catch (e) {}
                        });
                        return results;
                    }
                """)

        finally:
            await context.close()
            await browser.close()

    logger.info("Returning %d new releases for %s", len(releases), url)
    return releases


async def search_upcoming_reprints(series_name: str, date_str: Optional[str] = None) -> list[dict]:
    """
    Search the LoCG new-comics page for upcoming reprints of a given series.

    Uses the ?keyword= URL parameter which LoCG processes server-side — no search box
    interaction needed. The page renders matching comics in ul#comic-list-issues.

    URL pattern: /comics/new-comics/YYYY/MM/DD?keyword={series}+printing
    Discovered by inspecting #comic-list-block[data-search] on the live page.

    date_str: ISO date string like "2026-04-01". If None, uses current week.

    Returns list of dicts, deduplicated by locg_issue_id (one entry per issue,
    not per cover variant). Fields: locg_issue_id, title, issue_url, cover_image_url.
    """
    query = f"{series_name} printing"

    if date_str:
        # date_str is ISO "YYYY-MM-DD"; LoCG new-comics URL expects /YYYY/MM/DD
        parts = date_str.split("-")
        base_url = f"{BASE_URL}/comics/new-comics/{parts[0]}/{parts[1]}/{parts[2]}"
    else:
        base_url = f"{BASE_URL}/comics/new-comics"

    url = f"{base_url}?keyword={quote_plus(query)}"

    results: list[dict] = []
    async with async_playwright() as pw:
        browser, context = await _make_browser_context(pw)
        try:
            page = await context.new_page()

            logger.info("Searching for reprints: series=%r url=%s", series_name, url)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except Exception as exc:
                logger.warning("domcontentloaded timeout on new-comics reprint search: %s", exc)

            await asyncio.sleep(NAV_DELAY)

            # Parse ul#comic-list-issues > li.issue
            # Each li has data-parent (locg_issue_id) and data-comic (cover variant id).
            # Multiple li rows may share the same data-parent — one per cover variant.
            # Return ALL variants; dedup is handled upstream per (issue_id, cover_image_url).
            raw_items = await page.evaluate(f"""
                () => {{
                    const seen = new Set();
                    const out = [];
                    const items = document.querySelectorAll('#comic-list-issues li.issue');
                    items.forEach(li => {{
                        try {{
                            const issueId = li.getAttribute('data-parent');
                            if (!issueId) return;

                            const coverId = li.getAttribute('data-comic');
                            const link = li.querySelector('a[href*="/comic/"]');
                            // Strip ?variant=... to get clean issue URL
                            const issueUrl = link
                                ? link.href.split('?')[0]
                                : null;

                            // Title: prefer the link's title attribute, fall back to text
                            const titleEl = li.querySelector('a[title], h3, h4, .title, .name');
                            const title = titleEl
                                ? (titleEl.getAttribute('title') || titleEl.textContent.trim())
                                : (link ? link.textContent.trim() : '');

                            const img = li.querySelector('img');
                            const coverUrl = img
                                ? (img.getAttribute('src') || img.getAttribute('data-src'))
                                : null;

                            // Deduplicate by (issueId, coverUrl) so same variant isn't double-counted
                            const key = issueId + '|' + (coverUrl || coverId || '');
                            if (seen.has(key)) return;
                            seen.add(key);

                            out.push({{
                                locg_issue_id: issueId,
                                locg_cover_id: coverId,
                                title: title.replace(/\\s+/g, ' ').trim(),
                                issue_url: issueUrl,
                                cover_image_url: coverUrl,
                            }});
                        }} catch (e) {{}}
                    }});
                    return out;
                }}
            """)

            results = raw_items or []

        finally:
            await context.close()
            await browser.close()

    logger.info("Found %d unique issues for reprint search: series=%r date=%s", len(results), series_name, date_str)
    return results
