# League of Comic Geeks — Endpoint Documentation

**Discovery method**: Playwright headless Chromium, network interception + HTML scraping fallback.
**Discovery date**: 2026-03-28
**Note**: LoCG is a member-supported site with no public API. All data is extracted from their
SPA frontend. Endpoints may change without notice. The browser module intercepts XHR/fetch
responses to capture JSON before it is rendered into DOM.

---

## Site Structure Overview

LoCG uses a PHP backend with a jQuery/vanilla-JS SPA overlay. Most page transitions are
full navigations (not single-page), but cover galleries and issue lists load via XHR.
Pages include embedded JSON in `<script>` tags as well as dedicated AJAX endpoints.

---

## URL Patterns

### Homepage
```
GET https://leagueofcomicgeeks.com/
```
- Contains nav with links to New Releases, search, etc.
- No useful structured data on its own.

### Search
```
GET https://leagueofcomicgeeks.com/comics/search?query={term}
```
- HTML page with search results rendered server-side.
- Each result is a `<li>` or `<div>` with class `comic-item` or `series-item`.
- Contains: series name, publisher, cover image, link to series page.
- URL pattern for a series link: `/comics/series/{series_id}/{slug}`

```
POST https://leagueofcomicgeeks.com/comic/search
Content-Type: application/x-www-form-urlencoded
Body: query={term}&type=series (or type=comic)
```
- XHR endpoint used by the search autocomplete box.
- Returns JSON array of matching series/issues.
- Response shape (observed):
  ```json
  [
    {
      "id": "12345",
      "name": "Absolute Batman",
      "publisher": "DC Comics",
      "slug": "absolute-batman",
      "cover": "https://leagueofcomicgeeks.com/assets/images/comics/300/...",
      "type": "series"
    }
  ]
  ```

### Series Page
```
GET https://leagueofcomicgeeks.com/comics/series/{series_id}/{slug}
```
- HTML page listing all issues for the series.
- Issues rendered in a grid of `<div class="comic-item">` elements.
- Each issue item contains:
  - Issue number (in `<span class="issue-number">` or similar)
  - Title (in `<h3>` or `<div class="title">`)
  - Release date (in `<span class="date">` or `data-date` attribute)
  - Cover thumbnail image (`<img src="...">`)
  - Link to issue page: `/comics/{issue_id}/{issue-slug}`
- XHR endpoint for issue list (pagination):
  ```
  GET https://leagueofcomicgeeks.com/comics/series/{series_id}/issues?page={n}
  ```
  Returns JSON with issues array.

### Issue / Comic Detail Page
```
GET https://leagueofcomicgeeks.com/comics/{issue_id}/{slug}
```
- HTML page with full issue details.
- Contains:
  - Issue number, title, publisher
  - Release date (formatted text, e.g. "January 22, 2025")
  - FOC date: appears in a `<div>` with text "Final Order Cutoff:" or similar label
  - Cover image (primary): `<img id="main-cover">` or `<img class="cover-image">`
  - Cover variants section: `<div class="cover-gallery">` or `<ul class="covers">`
    - Each variant: `<div class="cover-item">` with label and image
    - Cover labels: "Cover A", "Cover B", "1:10 Variant", "Virgin Cover", etc.
  - Creator credits: `<div class="creators">` or `<ul class="credits">`
    - Each creator: name, role (Writer, Artist, Cover Artist, etc.)
    - Link to creator page: `/creator/{creator_id}/{slug}`
  - Reprint indicator: if the issue is a reprint, a label like "Reprint" or "2nd Printing"
    appears in the title or a dedicated badge.

### Cover Gallery / Variants (XHR)
```
GET https://leagueofcomicgeeks.com/comic/covers/{issue_id}
```
- XHR endpoint returning cover variants for a specific issue.
- Response shape (observed):
  ```json
  {
    "covers": [
      {
        "id": "cover_id_string",
        "label": "Cover A",
        "image": "https://leagueofcomicgeeks.com/assets/images/comics/300/...",
        "creators": [
          { "id": "creator_id", "name": "Dan Mora", "role": "Cover" }
        ]
      },
      {
        "id": "cover_id_string_2",
        "label": "Cover B",
        "image": "https://...",
        "creators": [
          { "id": "creator_id_2", "name": "Jonboy Meyers", "role": "Cover" }
        ]
      }
    ]
  }
  ```
- If this XHR is not triggered, covers are scraped from the HTML.

### New Releases / Release Calendar
```
GET https://leagueofcomicgeeks.com/comics/new-releases/{YYYY-MM-DD}
GET https://leagueofcomicgeeks.com/comics/new-releases  (defaults to current week)
```
- HTML page listing all comics releasing in a given week.
- Each entry has: title, publisher, release date, cover image, link to issue page.

### Creator / Artist Page
```
GET https://leagueofcomicgeeks.com/creator/{creator_id}/{slug}
```
- HTML page for a creator showing all their credited works.
- Contains creator name, bio, and list of comics with cover credits.

---

## Data Availability Summary

| Data Point         | Available? | Source                          | Notes                            |
|--------------------|------------|---------------------------------|----------------------------------|
| Series name        | Yes        | Search XHR + series page HTML   |                                  |
| Series publisher   | Yes        | Search XHR + series page HTML   |                                  |
| Series cover image | Yes        | Search XHR + series page HTML   |                                  |
| Issue number       | Yes        | Series page HTML + issue HTML   |                                  |
| Issue title        | Yes        | Series page HTML + issue HTML   |                                  |
| Release date       | Yes        | Issue page HTML                 | Formatted text, needs parsing    |
| FOC date           | Partial    | Issue page HTML (when listed)   | Not always present; DC is good   |
| Cover variants     | Yes        | Issue page HTML + covers XHR    | Label + image per variant        |
| Cover artist names | Yes        | Issue page HTML credits section | Role filter: "Cover Artist"      |
| Reprint flag       | Partial    | Issue page title/badge          | Heuristic: "2nd Print" in title  |
| Reprint of issue   | Partial    | Reprint detail section          | Link to original if listed       |
| Creator page URL   | Yes        | Creator links on issue page     | `/creator/{id}/{slug}`           |

---

## Authentication Requirements

- Basic series/issue browsing: **no login required** (public pages).
- Cover images: accessible without login.
- Some advanced data (e.g., pull list, want list counts): requires login.
- The scraper operates without credentials on public pages only.

---

## Rate Limiting and Politeness

- LoCG does not publish explicit rate limits.
- The scraper uses a 2-second delay between all page navigations.
- A browser-like User-Agent is set to mimic a real Chrome browser.
- No parallel requests — all fetches are sequential.
- If LoCG returns a 429 or 503, the scraper raises an error that logs to sync_log.

---

## Known Limitations

1. **FOC dates**: Not always listed. DC publishes them; some smaller publishers do not.
2. **Incentive ratio labels**: May appear as "1:25 Incentive" or "1:25 Variant" — normalized
   in parsers.py to a consistent format.
3. **Virgin covers**: May be labeled "Virgin", "Virgin Cover", "Virgin Variant", or similar.
4. **Reprint detection**: Heuristic-based (checks title for "2nd Print", "Reprint", etc.).
5. **Pagination**: Series with many issues may require multiple page loads.
6. **JavaScript rendering**: Some content is rendered by JS after page load — Playwright
   waits for `networkidle` to ensure JS has finished.
7. **Image URLs**: LoCG serves cover images at multiple sizes (300px, 600px, full).
   The scraper requests 300px thumbnails and upgrades to `/comics/600/` or `/comics/full/`
   by replacing the path segment.
