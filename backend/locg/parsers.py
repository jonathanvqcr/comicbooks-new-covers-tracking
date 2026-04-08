"""
LoCG Parsers — transform raw data from browser.py into clean Python dicts
matching the shapes defined in backend/schemas.py.

All parse_* functions accept raw dicts from the browser and return clean dicts.
They never raise; on any error they return partial data and log a warning.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%d",           # 2025-01-22
    "%B %d, %Y",          # January 22, 2025
    "%B %d %Y",           # January 22 2025
    "%b %d, %Y",          # Jan 22, 2025
    "%b %d %Y",           # Jan 22 2025
    "%m/%d/%Y",           # 01/22/2025
    "%d %B %Y",           # 22 January 2025
]


# Year-less formats (LoCG often omits the year for FOC dates, e.g. "May 18th")
_DATE_FORMATS_NO_YEAR = [
    "%B %d",    # January 22
    "%b %d",    # Jan 22
    "%B %dth",  # January 22th (in case ordinal stripping didn't fire)
    "%b %dth",  # Jan 22th
]


def _parse_date(raw: str | None) -> Optional[date]:
    """
    Try multiple date formats and return a date object or None.

    Handles year-less dates like "May 18th" by inferring the year:
    - Uses current year
    - If that date is already more than 30 days in the past, uses next year
    (LoCG sometimes omits the year for FOC/release dates of upcoming issues)
    """
    if not raw:
        return None
    raw = raw.strip().rstrip(",").strip()
    # Remove ordinal suffixes (1st, 2nd, 3rd, 4th → 1, 2, 3, 4)
    raw = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", raw)
    # Collapse multiple spaces
    raw = re.sub(r"\s+", " ", raw)

    # Try formats with explicit year first
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    # Try year-less formats — infer year from context
    for fmt in _DATE_FORMATS_NO_YEAR:
        try:
            parsed = datetime.strptime(raw, fmt)
            today = date.today()
            candidate = parsed.replace(year=today.year).date()
            # If the candidate is more than 180 days in the past, advance to next year
            # (FOC dates are typically 4-8 weeks before release; 180 days covers even
            #  past issues while correctly handling upcoming FOC dates)
            if (today - candidate).days > 180:
                candidate = parsed.replace(year=today.year + 1).date()
            return candidate
        except ValueError:
            continue

    logger.debug("Could not parse date string: %r", raw)
    return None


def _clean_str(value: str | None) -> Optional[str]:
    """Strip and return None if empty."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _extract_locg_id_from_url(url: str | None, pattern: str = r"/(\d+)") -> Optional[str]:
    """Extract a numeric ID from a URL using a regex pattern."""
    if not url:
        return None
    m = re.search(pattern, url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------

def parse_series(raw: dict) -> dict:
    """
    Transform raw LoCG series data into a dict matching SeriesRead fields.

    Input (from search_series or series page scrape):
        name, locg_series_id, slug, publisher, cover_image_url, url, type

    Output:
        locg_series_id, name, publisher, slug, locg_url, cover_image_url
    """
    series_id = (
        _clean_str(raw.get("locg_series_id"))
        or _clean_str(raw.get("id"))
        or _extract_locg_id_from_url(raw.get("url"), r"/series/(\d+)")
    )
    slug = _clean_str(raw.get("slug"))
    url = _clean_str(raw.get("url"))
    # Reconstruct URL from ID + slug if only partial info available
    if not url and series_id:
        url = f"https://leagueofcomicgeeks.com/comics/series/{series_id}"
        if slug:
            url += f"/{slug}"

    return {
        "locg_series_id": series_id,
        "name": _clean_str(raw.get("name") or raw.get("title")) or "Unknown Series",
        "publisher": _clean_str(raw.get("publisher")),
        "slug": slug,
        "locg_url": url,
        "cover_image_url": _normalize_image_url(raw.get("cover") or raw.get("cover_image_url")),
    }


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

def parse_issue(raw: dict) -> dict:
    """
    Transform raw LoCG issue data into a dict compatible with Issue ORM model.

    Input: raw dict from get_series_issues or get_issue_detail
    Output: dict with keys matching Issue model columns
    """
    issue_id = (
        _clean_str(raw.get("locg_issue_id"))
        or _clean_str(raw.get("comic_id"))
        or _clean_str(raw.get("id"))
        or _extract_locg_id_from_url(raw.get("issue_url") or raw.get("url"))
    )

    # Title — prefer full_title over title
    title = _clean_str(raw.get("full_title") or raw.get("title") or raw.get("name"))

    # Issue number — clean up leading # and whitespace
    issue_number = _clean_str(raw.get("issue_number") or raw.get("number"))
    if issue_number:
        issue_number = issue_number.lstrip("#").strip()
        # If it looks like a full title with issue number embedded, extract just the number
        m = re.search(r"#(\d+[A-Za-z]*)", issue_number)
        if m:
            issue_number = m.group(1)

    release_date = _parse_date(raw.get("release_date_raw") or raw.get("release_date"))
    foc_date = _parse_date(raw.get("foc_date_raw") or raw.get("foc_date"))

    # Reprint detection: explicit flag or heuristic from title
    is_reprint = bool(raw.get("is_reprint", False))
    if not is_reprint and title:
        tl = title.lower()
        is_reprint = any(kw in tl for kw in [
            "2nd print", "second print", "3rd print", "reprint",
            "facsimile", "second printing", "2nd printing",
        ])

    # Series info from detail page
    locg_series_id = (
        _clean_str(raw.get("locg_series_id"))
        or _extract_locg_id_from_url(raw.get("series_url"), r"/series/(\d+)")
    )

    return {
        "locg_issue_id": issue_id,
        "locg_series_id": locg_series_id,  # used to find/create series FK
        "issue_number": issue_number,
        "title": title,
        "release_date": release_date,
        "foc_date": foc_date,
        "is_reprint": is_reprint,
        "cover_image_url": _normalize_image_url(raw.get("cover_image_url") or raw.get("cover")),
        "issue_url": _clean_str(raw.get("issue_url") or raw.get("url")),
    }


# ---------------------------------------------------------------------------
# Covers
# ---------------------------------------------------------------------------

def parse_issue_covers(raw_issue: dict) -> list[dict]:
    """
    Extract cover variants from a raw issue detail dict.

    Input: raw dict from get_issue_detail (has 'covers' list)
    Output: list of dicts with keys: cover_label, cover_image_url, locg_cover_id, artists

    Each artist in the artists list is a dict:
        { name, locg_creator_id, locg_url }
    """
    raw_covers = raw_issue.get("covers") or []

    # If no variants listed, synthesize a single "Cover A" from the primary cover image
    if not raw_covers:
        primary_img = _normalize_image_url(
            raw_issue.get("cover_image_url") or raw_issue.get("cover")
        )
        if primary_img:
            return [{
                "cover_label": "Cover A",
                "cover_image_url": primary_img,
                "locg_cover_id": None,
                "artists": [],
            }]
        return []

    results = []
    for cover in raw_covers:
        if not isinstance(cover, dict):
            continue

        cover_id = (
            _clean_str(cover.get("locg_cover_id"))
            or _clean_str(cover.get("id"))
        )

        label = _clean_str(
            cover.get("cover_label")
            or cover.get("label")
            or cover.get("name")
        )
        label = _normalize_cover_label(label)

        image_url = _normalize_image_url(
            cover.get("cover_image_url")
            or cover.get("image")
            or cover.get("img")
        )

        # Artists on this cover
        raw_artists = cover.get("artists") or cover.get("creators") or []
        artists = [parse_artist(a) for a in raw_artists if isinstance(a, dict)]
        # Filter out empty entries
        artists = [a for a in artists if a.get("name")]

        results.append({
            "cover_label": label,
            "cover_image_url": image_url,
            "locg_cover_id": cover_id,
            "artists": artists,
        })

    return results


def parse_issue_creators(raw_issue: dict) -> list[dict]:
    """
    Extract all creator credits from a raw issue detail dict.
    Filters to cover-related roles only.

    Returns list of artist dicts: { name, locg_creator_id, locg_url, role }
    """
    raw_creators = raw_issue.get("creators") or []
    results = []
    cover_roles = {
        "cover", "cover artist", "cover art", "cover design",
        "variant cover", "incentive cover", "cover a", "cover b",
        "artist",  # sometimes generic "artist" means cover artist
    }
    for creator in raw_creators:
        if not isinstance(creator, dict):
            continue
        role = _clean_str(creator.get("role") or "") or ""
        role_lower = role.lower()
        # Include if role is cover-related or no role specified (assume cover)
        if not role_lower or any(r in role_lower for r in cover_roles):
            parsed = parse_artist(creator)
            if parsed.get("name"):
                parsed["role"] = role
                results.append(parsed)
    return results


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

def parse_artist(raw: dict) -> dict:
    """
    Transform raw LoCG creator data into a dict matching Artist ORM model.

    Input: dict with keys like name, locg_creator_id, locg_url, id, url, role
    Output: { name, locg_creator_id, locg_url }
    """
    creator_id = (
        _clean_str(raw.get("locg_creator_id"))
        or _clean_str(raw.get("creator_id"))
        or _clean_str(raw.get("id"))
        or _extract_locg_id_from_url(raw.get("locg_url") or raw.get("url"), r"/creator/(\d+)")
    )

    url = _clean_str(raw.get("locg_url") or raw.get("url"))

    return {
        "name": _clean_str(raw.get("name") or raw.get("creator_name")) or "",
        "locg_creator_id": creator_id,
        "locg_url": url,
    }


# ---------------------------------------------------------------------------
# Label normalization
# ---------------------------------------------------------------------------

_LABEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Incentive ratios: "1:25 Variant", "1:10 Incentive", "25 Copy Incentive"
    (re.compile(r"1[:/](\d+)\s*(?:incentive|variant|cover)?", re.I), lambda m: f"1:{m.group(1)} Incentive"),
    (re.compile(r"(\d+)\s*copy\s*incentive", re.I), lambda m: f"1:{m.group(1)} Incentive"),
    # Virgin covers
    (re.compile(r"virgin\s*(?:cover|variant)?", re.I), lambda m: "Virgin Cover"),
    # Foil covers
    (re.compile(r"foil\s*(?:cover|variant)?", re.I), lambda m: "Foil Cover"),
    # Blank sketch covers
    (re.compile(r"blank\s*(?:cover|sketch|variant)?", re.I), lambda m: "Blank Cover"),
    # Gatefold
    (re.compile(r"gatefold", re.I), lambda m: "Gatefold Cover"),
]


def _normalize_cover_label(label: str | None) -> Optional[str]:
    """
    Normalize cover variant labels to a consistent format.
    Examples:
        "1:25 Variant" → "1:25 Incentive"
        "25 Copy Incentive" → "1:25 Incentive"
        "Virgin Cover" → "Virgin Cover"
        "Cover A" → "Cover A"  (unchanged)
    """
    if not label:
        return None
    label = label.strip()
    for pattern, formatter in _LABEL_PATTERNS:
        # Only fullmatch — never use search(), which would collapse rich labels like
        # "Cover E Bengal Foil Virgin Variant" → "Virgin Cover" (losing all identifying info)
        m = pattern.fullmatch(label)
        if m:
            return formatter(m) if callable(formatter) else formatter
    return label


# ---------------------------------------------------------------------------
# Image URL normalization
# ---------------------------------------------------------------------------

def _normalize_image_url(url: str | None, size: str = "300") -> Optional[str]:
    """
    Normalize LoCG image URLs to request a consistent size.
    LoCG serves: /assets/images/comics/{size}/{filename}
    Default to 300px thumbnails.
    """
    if not url:
        return None
    url = url.strip()
    if not url.startswith("http"):
        if url.startswith("/"):
            url = f"https://leagueofcomicgeeks.com{url}"
        else:
            return url
    # Replace size segment: /comics/600/ → /comics/300/
    url = re.sub(r"/comics/(\d+)/", f"/comics/{size}/", url)
    return url
