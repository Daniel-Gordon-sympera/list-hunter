# 🕷️ Super Lawyers Deep Scraper — Comprehensive Plan v2.0

## 📋 Table of Contents
1. [Executive Summary](#1-executive-summary)
2. [Website Architecture Analysis](#2-website-architecture-analysis)
3. [Data Model (33 Columns)](#3-data-model-33-columns)
4. [Scraping Methodology](#4-scraping-methodology)
5. [Technical Architecture](#5-technical-architecture)
6. [Module-by-Module Implementation Plan](#6-module-by-module-implementation-plan)
7. [Anti-Detection & Resilience Strategy](#7-anti-detection--resilience-strategy)
8. [Error Handling & Recovery](#8-error-handling--recovery)
9. [Data Pipeline & Output](#9-data-pipeline--output)
10. [Critical Review & Gap Analysis](#10-critical-review--gap-analysis)
11. [Risk Matrix](#11-risk-matrix)
12. [Execution Timeline](#12-execution-timeline)
13. [Appendices](#13-appendices)

---

## 1. Executive Summary

**Objective:** Build a fully functional Python-based deep scraper that accepts a **location input** (city/state or ZIP code) and produces a **CSV file** with one row per attorney, containing **33 structured columns** across 7 logical groups — covering identity, location, contact, professional profile, achievements, social presence, and scrape metadata.

**Target:** `https://attorneys.superlawyers.com/` + `https://profiles.superlawyers.com/`

**Estimated Scale:** ~100,000+ attorney profiles across the U.S.

**Input → Output Flow:**
```
User Input (e.g., "Los Angeles, CA" or "90210")
        │
        ▼
┌──────────────────────────────────┐
│  Phase 1: Location Resolver      │  → Resolves to state-slug + city-slug
│  (city/state or ZIP)             │     e.g. california/los-angeles
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  Phase 2: Practice Area Discovery│  → Fetches city page, extracts
│                                  │     all practice-area slugs (~130)
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  Phase 3: Listing Page Crawler   │  → Paginates per practice area
│  (paginated listing pages)       │     Extracts profile URLs + pre-fills
│                                  │     7 fields from listing cards
│                                  │     Deduplicates by UUID
└──────────┬───────────────────────┘
           │  unique profile URLs
           ▼
┌──────────────────────────────────┐
│  Phase 4: Profile Deep Scraper   │  → Fetches each profile page
│  (individual profiles)           │     Extracts all 33 fields
│                                  │     Detects profile tier
│                                  │     Flags auto-generated bios
└──────────┬───────────────────────┘
           │
           ▼
┌──────────────────────────────────┐
│  Phase 5: Export                 │  → Writes CSV / JSONL / SQLite
│  (clean, de-duped, encoded)      │     with data cleaning pipeline
└──────────────────────────────────┘
```

---

## 2. Website Architecture Analysis

### 2.1 Domain & Subdomain Structure

```
superlawyers.com (Main Domain Tree)
│
├── www.superlawyers.com          → Marketing/editorial content (NOT scraped)
│   ├── /about/                   → About pages
│   ├── /resources/               → Legal articles
│   ├── /top-lists/               → Top lists by region
│   └── /articles/                → Feature articles
│
├── attorneys.superlawyers.com    → DIRECTORY (Primary Entry Point) ★
│   ├── /                         → Homepage with state/city/practice area links
│   ├── /{state}/                 → State index (lists all cities in that state)
│   ├── /{state}/{city}/          → City index (lists all practice areas for city)
│   ├── /{practice-area}/         → Practice area index (lists all states)
│   ├── /{practice-area}/{state}/ → Practice area + state (lists cities)
│   ├── /{practice-area}/{state}/{city}/          → LISTING PAGES ★
│   │   └── ?page=N               → Paginated listing pages
│   ├── /search                   → Search results endpoint
│   └── /advanced_search/         → Advanced search form
│
├── profiles.superlawyers.com     → INDIVIDUAL PROFILES ★★
│   ├── /{state}/{city}/lawyer/{name-slug}/{uuid}.html  → Attorney profile
│   ├── /{state}/{city}/lawfirm/{name-slug}/{uuid}.html → Firm profile
│   ├── /contact/lawyer/{uuid}.html                     → Contact form
│   ├── /badges/lawyer/{uuid}.html                      → Badge page
│   └── /location/                                      → Location index
│
├── my.superlawyers.com           → Attorney login portal (NOT scraped)
├── answers.superlawyers.com      → Q&A platform (NOT scraped)
└── lawschools.superlawyers.com   → Law school directory (NOT scraped)
```

### 2.2 URL Pattern Analysis

#### Listing Pages (attorneys.superlawyers.com)

| Pattern | Example | Description |
|---------|---------|-------------|
| `/{state}/` | `/california/` | State-level — lists all cities |
| `/{state}/{city}/` | `/california/los-angeles/` | City-level — lists all practice areas |
| `/{practice-area}/{state}/{city}/` | `/business-and-corporate/california/long-beach/` | Practice+Location — **LISTING with attorneys** |
| `/{practice-area}/{state}/{city}/?page=N` | `...long-beach/?page=19` | Paginated listing |

**Key Insight:** To get ALL attorneys for a given city, we must:
1. Fetch `/{state}/{city}/` to discover available practice areas
2. Crawl every `/{practice-area}/{state}/{city}/` combination with pagination

#### Profile Pages (profiles.superlawyers.com)

| Component | Value |
|-----------|-------|
| Full URL | `https://profiles.superlawyers.com/california/beverly-hills/lawyer/nadira-t-imam/e926cca4-e9fb-497f-b993-c937da341408.html` |
| Pattern | `/{state}/{city}/lawyer/{name-slug}/{uuid}.html` |
| UUID | `e926cca4-e9fb-497f-b993-c937da341408` — globally unique per attorney |

### 2.3 Page Content — Listing Pages

Each listing page contains **~15 attorney cards** per page. Each card yields:

| Field Extractable | Source Element |
|-------------------|----------------|
| `uuid` | Profile link href → regex UUID |
| `name` | `<h2>` → `<a>` text |
| `firm_name` | Text/link below name |
| `phone` | `<a href="tel:+1...">` |
| `description` | Tagline paragraph |
| `selection_type` | Badge text: "Super Lawyers" or "Rising Stars" |
| `profile_url` | "View profile" `href` (strip `?adSubId`) |

**These 7 pre-fill fields are captured during Phase 3 as a fallback** — if a profile page fails to load, we still have partial data.

### 2.4 Page Content — Profile Pages

Profile pages are **server-side rendered HTML** (no SPA/JS). Content is organized in sections:

| Section | Fields Extractable | Profile Tier |
|---------|-------------------|:---:|
| **Header** | name, firm_name, phone, practice_areas (summary), licensed_since, education, languages, selection_type, selection_years | ALL |
| **Office Location** | street, city, state, zip_code, geo_coordinates | ALL |
| **About** | about (narrative bio) | Expanded+ (auto-gen on Basic) |
| **Practice Areas** | practice_areas, focus_areas, % breakdown | Expanded+ |
| **Bar / Professional Activity** | bar_activity | Premium |
| **Honors** | honors | Premium |
| **Representative Clients** | (folded into bar_activity) | Premium |
| **Transactions** | (folded into bar_activity) | Premium |
| **Pro Bono / Community Service** | pro_bono | Premium |
| **Scholarly Lectures / Writings** | publications | Premium |
| **Selections** | selection_type, selection_years | ALL |
| **Find Me Online** | linkedin_url, facebook_url, twitter_url, firm_website_url | Premium |
| **Additional Sources** | findlaw_url | Usually |
| **Professional Webpage** | professional_webpage_url | Premium |
| **Google Maps Embed** | geo_coordinates (from static map URL) | ALL |

### 2.5 Profile Tiers (Critical Discovery)

The site has **three distinct profile tiers** with dramatically different data availability:

| Feature | Basic | Expanded | Premium |
|---------|:-----:|:--------:|:-------:|
| Name, Firm, Address | ✅ | ✅ | ✅ |
| Practice areas | ✅ | ✅ | ✅ |
| Licensed since, Education | ✅ | ✅ | ✅ |
| Selection type + years | ✅ | ✅ | ✅ |
| Phone number | ❌ | ✅ | ✅ |
| Photo | ❌ | ✅ | ✅ |
| About (real bio) | 🤖 Auto | ✅ | ✅ |
| Languages | ❌ | ⚠️ | ✅ |
| Focus areas | ❌ | ⚠️ | ✅ |
| Practice area % breakdown | ❌ | ❌ | ✅ |
| Bar / Professional Activity | ❌ | ⚠️ | ✅ |
| Honors | ❌ | ⚠️ | ✅ |
| Pro Bono / Community | ❌ | ❌ | ✅ |
| Publications / Lectures | ❌ | ❌ | ✅ |
| Social links | ❌ | ⚠️ | ✅ |
| Website URL | ❌ | ⚠️ | ✅ |
| Professional Webpage | ❌ | ❌ | ✅ |

🤖 = Auto-generated boilerplate text | ⚠️ = Sometimes present

**Implication:** The scraper must gracefully handle empty fields and detect auto-generated bios.

### 2.6 Technology Stack Observations

- **Server:** Apache/PHP (indicated by `Page Generated: 0.054s` footer)
- **CDN:** Cloudflare for images (`cdn.superlawyers.com`) + Cloudinary transforms
- **Maps:** Google Maps Static API (embeds lat/lng in URL — free geo data)
- **Frontend:** Server-rendered HTML, jQuery for autocomplete — no SPA framework
- **Bot Protection:** Returns 403 on root URL with basic User-Agent; no Cloudflare Turnstile or heavy WAF detected
- **Rate Limits:** Not aggressively enforced but polite crawling recommended

---

## 3. Data Model (33 Columns)

### 3.1 Schema Overview — 7 Groups

| Group | # Cols | Columns | Always Available? |
|-------|:------:|---------|:-:|
| **A — Identity** | 6 | `uuid`, `name`, `firm_name`, `selection_type`, `selection_years`, `description` | Mostly ✅ |
| **B — Location** | 6 | `street`, `city`, `state`, `zip_code`, `country`, `geo_coordinates` | ✅ Yes |
| **C — Contact** | 4 | `phone`, `email`, `firm_website_url`, `professional_webpage_url` | ⚠️ Expanded+ |
| **D — Professional** | 6 | `about`, `practice_areas`, `focus_areas`, `licensed_since`, `education`, `languages` | Mixed |
| **E — Achievements** | 4 | `honors`, `bar_activity`, `pro_bono`, `publications` | ⚠️ Premium only |
| **F — Social** | 4 | `linkedin_url`, `facebook_url`, `twitter_url`, `findlaw_url` | ⚠️ Premium only |
| **G — Metadata** | 3 | `profile_url`, `profile_tier`, `scraped_at` | ✅ Always |

### 3.2 Full Column Specification

#### Group A — Identity

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 1 | `uuid` | string | `e926cca4-e9fb-497f-b993-c937da341408` | Profile URL |
| 2 | `name` | string | `Nadira T. Imam` | `<h1>` on profile |
| 3 | `firm_name` | string | `Law Offices of Lawrence H. Jacobson` | Firm link on profile |
| 4 | `selection_type` | enum | `Super Lawyers` \| `Rising Stars` | Badge/selection text |
| 5 | `selection_years` | string | `2020-2026` | Selection section |
| 6 | `description` | string | `Super Lawyer with multiple years experience...` | Listing card tagline |

#### Group B — Location

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 7 | `street` | string | `9777 Wilshire Blvd., Suite 517` | Office location block |
| 8 | `city` | string | `Beverly Hills` | Address last line parse |
| 9 | `state` | string | `CA` | Address last line parse |
| 10 | `zip_code` | string | `90212` | Address regex `\d{5}` |
| 11 | `country` | string | `United States` | Hardcoded |
| 12 | `geo_coordinates` | string | `34.067070,-118.397720` | Google Maps static URL |

#### Group C — Contact

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 13 | `phone` | string | `310-271-0747` | `a[href^="tel:"]` |
| 14 | `email` | string | *(rare)* | `a[href^="mailto:"]` or regex in bio |
| 15 | `firm_website_url` | string | `https://www.lawrencejacobson.com` | "Visit website" link |
| 16 | `professional_webpage_url` | string | `.../attorney/nadira-imam/` | "Professional Webpage:" text |

#### Group D — Professional Profile

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 17 | `about` | text | *(multi-paragraph bio)* | About section paragraphs |
| 18 | `practice_areas` | delimited | `Business/Corporate ; Real Estate: Business ; Estate Planning & Probate` | Practice areas heading |
| 19 | `focus_areas` | delimited | `Business Formation and Planning ; Contracts ; Trusts ; Wills` | Focus areas heading |
| 20 | `licensed_since` | string | `2017, California` | "Licensed in X since:" or "First Admitted:" |
| 21 | `education` | string | `Abraham Lincoln University School of Law` | Education link / section |
| 22 | `languages` | delimited | `English ; German ; Spanish` | "Languages spoken:" text |

#### Group E — Achievements

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 23 | `honors` | delimited | `Lawrence J. Blake Award, BHBA (2015) ; Board of Governors Award, BHBA (2018)` | "Honors" section |
| 24 | `bar_activity` | delimited | `President, BHBA (2024) ; President Elect, BHBA (2023)` | "Bar / Professional Activity" section |
| 25 | `pro_bono` | delimited | `Roxbury Park Legal Clinic (2018) ; Samoshel Homeless Shelter (2015)` | "Pro bono / Community Service" section |
| 26 | `publications` | delimited | `Speaker: Mental Health... (BHBA, 2023)` | "Scholarly Lectures / Writings" section |

#### Group F — Social & Web Presence

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 27 | `linkedin_url` | string | `https://www.linkedin.com/in/nadira-imam-334329b5/` | "Find me online" section |
| 28 | `facebook_url` | string | | "Find me online" section |
| 29 | `twitter_url` | string | | "Find me online" section |
| 30 | `findlaw_url` | string | `https://lawyers.findlaw.com/.../nadira-imam-.../` | "Additional sources" section |

#### Group G — Metadata

| # | Column | Type | Example | Source |
|---|--------|------|---------|--------|
| 31 | `profile_url` | string | `https://profiles.superlawyers.com/.../e926cca4...html` | Canonical profile URL |
| 32 | `profile_tier` | enum | `basic` \| `expanded` \| `premium` | Inferred from data completeness |
| 33 | `scraped_at` | ISO 8601 | `2026-02-10T14:30:22Z` | Timestamp at scrape time |

### 3.3 Python Data Model

```python
from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timezone
import re


@dataclass
class AttorneyRecord:
    """Complete attorney data model — 33 fields across 7 groups."""

    # ── Group A: Identity ──
    uuid: str = ""
    name: str = ""
    firm_name: str = ""
    selection_type: str = ""          # "Super Lawyers" | "Rising Stars"
    selection_years: str = ""         # "2020-2026"
    description: str = ""             # Tagline from listing card

    # ── Group B: Location ──
    street: str = ""
    city: str = ""
    state: str = ""                   # 2-letter abbreviation
    zip_code: str = ""
    country: str = "United States"
    geo_coordinates: str = ""         # "lat,lng"

    # ── Group C: Contact ──
    phone: str = ""
    email: str = ""
    firm_website_url: str = ""
    professional_webpage_url: str = ""

    # ── Group D: Professional Profile ──
    about: str = ""
    practice_areas: str = ""          # "; "-delimited
    focus_areas: str = ""             # "; "-delimited
    licensed_since: str = ""
    education: str = ""
    languages: str = ""               # "; "-delimited

    # ── Group E: Achievements ──
    honors: str = ""                  # "; "-delimited
    bar_activity: str = ""            # "; "-delimited
    pro_bono: str = ""                # "; "-delimited
    publications: str = ""            # "; "-delimited

    # ── Group F: Social & Web ──
    linkedin_url: str = ""
    facebook_url: str = ""
    twitter_url: str = ""
    findlaw_url: str = ""

    # ── Group G: Metadata ──
    profile_url: str = ""
    profile_tier: str = ""            # "basic" | "expanded" | "premium"
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # ── Helpers ──
    @classmethod
    def csv_headers(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_csv_row(self) -> list[str]:
        return [getattr(self, f.name) for f in fields(self)]

    def to_dict(self) -> dict:
        return asdict(self)

    def completeness_score(self) -> float:
        """0.0-1.0 ratio of non-empty fields."""
        filled = sum(1 for f in fields(self) if getattr(self, f.name))
        return round(filled / len(fields(self)), 2)

    def infer_profile_tier(self) -> str:
        """Detect profile tier from available data."""
        if self.bar_activity or self.pro_bono or self.publications:
            return "premium"
        elif self.phone and self.about and not self._is_auto_bio():
            return "expanded"
        return "basic"

    def _is_auto_bio(self) -> bool:
        """Detect boilerplate auto-generated bios on basic profiles."""
        patterns = [
            r"^[\w\s.]+ is an attorney who represents clients in the",
            r"Being selected to Super Lawyers is limited to a small number",
            r"passed the bar exam and was admitted to legal practice in",
        ]
        return any(re.search(p, self.about) for p in patterns)
```

### 3.4 Multi-Value Field Encoding

```
Delimiter:    " ; "  (space-semicolon-space)
Escaping:     Semicolons within a value → replaced with comma
CSV quoting:  QUOTE_ALL (every cell double-quoted)
Encoding:     UTF-8 with BOM (utf-8-sig) for Excel compatibility
Max length:   10,000 chars per cell (truncate with "... [truncated]")
```

---

## 4. Scraping Methodology

### 4.1 Strategy: Location-First, Practice-Area-Aware Crawl

```
Phase 1: Location Resolution
    Input "Los Angeles, CA" → slug: "california/los-angeles"
    Input "90210" → resolve to "california/beverly-hills"

Phase 2: Practice Area Discovery
    Fetch: attorneys.superlawyers.com/{state}/{city}/
    Parse: All practice area links on the city page
    Result: List of ~130 practice area slugs for that city

Phase 3: Listing Page Crawl (per practice area)
    For each practice area:
        Fetch: attorneys.superlawyers.com/{practice-area}/{state}/{city}/?page=N
        Paginate: page=1, 2, ... until empty results
        Extract: Profile URLs + pre-fill 7 fields from listing cards
        Deduplicate: By UUID (attorneys appear across multiple practice areas)
    Result: Set of unique (uuid → profile_url + partial AttorneyRecord)

Phase 4: Profile Deep Scrape
    For each unique UUID:
        Fetch: profiles.superlawyers.com/.../{uuid}.html
        Parse: All 33 fields
        Merge: Listing pre-fill data + profile deep data
        Detect: Profile tier (basic/expanded/premium)
        Flag: Auto-generated bios
    Result: List[AttorneyRecord]

Phase 5: Export
    Clean: Data cleaning pipeline (normalize phones, strip tracking params, etc.)
    Write: CSV (primary) + optional JSONL / SQLite
```

### 4.2 Why This Strategy

| Alternative | Rejected Because |
|-------------|------------------|
| Scrape listing pages only | Misses 26 of 33 fields (about, achievements, social, education, etc.) |
| Search endpoint only | Returns limited results, no reliable pagination, no full coverage |
| Sitemap-based crawl | No public sitemap.xml discovered |
| API-based approach | No public REST/GraphQL API available |
| Headless browser for all pages | Overkill — content is server-rendered HTML, no JS rendering needed |

### 4.3 Deduplication Strategy

Attorneys appear in **multiple practice area** listings for the same city. An attorney practicing "Business & Corporate" AND "Real Estate" appears on both listing pages.

**Dedup key:** The UUID from the profile URL.

```
https://profiles.superlawyers.com/.../e926cca4-e9fb-497f-b993-c937da341408.html
                                       ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲
                                       Globally unique — this IS the dedup key
```

Implementation: `seen_uuids: Set[str]` — skip any profile URL whose UUID is already in the set.

**Sponsored duplicate handling:** Sponsored attorney cards (with `?adSubId=XXXX`) also repeat in the regular list. UUID dedup handles this transparently.

### 4.4 Two-Phase Data Collection

A key architectural improvement: **pre-filling partial data from listing cards** before visiting profile pages.

```python
# Phase 3 output: partial record from listing card
partial = AttorneyRecord(
    uuid="e926cca4-...",
    name="Nadira T. Imam",
    firm_name="Law Offices of Lawrence H. Jacobson",
    phone="310-271-0747",
    description="Super Lawyer with multiple years...",
    selection_type="Rising Stars",
    profile_url="https://profiles.superlawyers.com/.../e926cca4...html"
)

# Phase 4: merge deep profile data into partial record
# If profile fetch fails → we still have 7 fields as fallback
```

**Benefits:**
- Partial data even if a profile page 403s or times out
- Reduces profile-page parsing failures since we already have the basics
- Pre-populates `selection_type` which is harder to parse from some profile layouts

---

## 5. Technical Architecture

### 5.1 Technology Stack

```python
# Core
Python 3.11+

# HTTP & Parsing
httpx              # Async HTTP client (HTTP/2, connection pooling)
beautifulsoup4     # HTML parsing
lxml               # Fast parser backend for BS4

# Async & Concurrency
asyncio            # Native async event loop
aiofiles           # Async file I/O for checkpoints

# Data
csv (stdlib)       # CSV writing
dataclasses        # Structured data models (AttorneyRecord)
json (stdlib)      # JSONL export + checkpoint files

# Utilities
tenacity           # Retry logic with exponential backoff
fake-useragent     # Rotating User-Agent strings
python-slugify     # Location string → URL slug normalization
uszipcode          # ZIP code → city/state resolution

# Optional
openpyxl           # Excel .xlsx export
```

### 5.2 Project Structure

```
superlawyers-scraper/
├── main.py                    # CLI entry point + orchestrator
├── config.py                  # All configuration constants
├── models.py                  # AttorneyRecord (33-field dataclass)
│
├── phases/
│   ├── phase1_resolver.py     # Location input → URL slug resolution
│   ├── phase2_discovery.py    # City page → practice area slug list
│   ├── phase3_listings.py     # Listing page crawler + pagination + pre-fill
│   ├── phase4_profiles.py     # Profile deep scraper (all 33 fields)
│   └── phase5_export.py       # CSV / JSONL / SQLite writer
│
├── parsers/
│   ├── listing_parser.py      # Parse listing card HTML → partial AttorneyRecord
│   ├── profile_parser.py      # Parse profile page HTML → full AttorneyRecord
│   └── address_parser.py      # Parse address block → street/city/state/zip
│
├── utils/
│   ├── http_client.py         # Configured httpx async client with retries
│   ├── rate_limiter.py        # Token-bucket rate limiter
│   ├── user_agents.py         # User-Agent rotation pool
│   ├── checkpoint.py          # Resume/checkpoint persistence
│   ├── geo_extractor.py       # Google Maps URL → lat/lng parser
│   └── bio_detector.py        # Auto-generated bio detection
│
├── tests/
│   ├── test_resolver.py
│   ├── test_listing_parser.py
│   ├── test_profile_parser.py
│   ├── test_address_parser.py
│   └── fixtures/              # Saved HTML pages for offline testing
│       ├── profile_premium.html
│       ├── profile_basic.html
│       └── listing_page.html
│
├── requirements.txt
└── README.md
```

### 5.3 Concurrency Model

```
┌──────────────────────────────────────────────────────────┐
│                    Main Orchestrator                      │
│  Phase 1 → Phase 2 → Phase 3 (sequential per PA)        │
│                        → Phase 4 (async worker pool)     │
│                        → Phase 5 (export)                │
└──────────────┬───────────────────────────────────────────┘
               │
   ┌───────────┴───────────────┐
   │  Phase 3: Listing Crawl   │  Sequential per practice area,
   │  (one PA at a time)       │  sequential pagination within each
   └───────────┬───────────────┘
               │ Unique profile URLs + partial records
               ▼
   ┌───────────────────────────┐
   │  Phase 4: Profile Pool    │  Async semaphore-bounded worker pool
   │  asyncio.Semaphore(5)     │  5 concurrent profile fetches
   │  + TokenBucketLimiter     │  ~1 req/sec average, random jitter
   │  + per-request delay      │  1-3 sec between requests
   └───────────┬───────────────┘
               │ Full AttorneyRecord objects
               ▼
   ┌───────────────────────────┐
   │  Phase 5: Export          │  Sequential write
   │  (single-threaded I/O)    │  CSV + optional JSONL
   └───────────────────────────┘
```

**Concurrency constraints:**
- Max 5 concurrent profile fetches (via `asyncio.Semaphore(5)`)
- 1–3 second random delay between each request
- Hard ceiling: 30 requests/minute
- Exponential backoff on 429/503 errors
- Session rotation every ~50 requests

---

## 6. Module-by-Module Implementation Plan

### 6.1 Module: `config.py`

```python
# URLs
BASE_URL = "https://attorneys.superlawyers.com"
PROFILE_BASE_URL = "https://profiles.superlawyers.com"

# Rate limiting
REQUEST_DELAY_MIN = 1.0        # seconds
REQUEST_DELAY_MAX = 3.0        # seconds
MAX_CONCURRENT = 5             # simultaneous profile fetches
MAX_REQUESTS_PER_MINUTE = 30
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0       # exponential: 2s → 4s → 8s

# Pagination
MAX_PAGES_PER_CATEGORY = 200   # safety limit per practice area

# Output
OUTPUT_DIR = "./output"
CSV_ENCODING = "utf-8-sig"
CSV_DELIMITER = ","
MULTIVALUE_DELIMITER = " ; "
MAX_CELL_LENGTH = 10_000

# Timeouts
REQUEST_TIMEOUT = 30           # seconds

# Checkpoint
CHECKPOINT_FILE = "checkpoint.json"
CHECKPOINT_INTERVAL = 25       # save every N profiles
```

### 6.2 Module: `models.py`

See Section 3.3 above for the full `AttorneyRecord` dataclass with all 33 fields, helper methods, profile tier inference, and auto-bio detection.

### 6.3 Module: `phases/phase1_resolver.py` — Location Resolution

**Responsibilities:**
1. Parse user input (city/state string or ZIP code)
2. Resolve to URL slugs: `(state_slug, city_slug)`
3. Validate the resolved path exists on the site (HTTP 200)

```python
class LocationResolver:
    """Resolve user location input to SuperLawyers URL slugs."""

    STATE_ABBREV_TO_SLUG = {
        "AL": "alabama", "AK": "alaska", "AZ": "arizona",
        "AR": "arkansas", "CA": "california", "CO": "colorado",
        # ... all 50 states + DC
        "DC": "washington-dc",
    }

    async def resolve(self, location_input: str) -> tuple[str, str]:
        """
        Input:  "Los Angeles, CA" or "90210"
        Output: ("california", "los-angeles")
        """
        location_input = location_input.strip()

        if self._is_zipcode(location_input):
            city, state_abbrev = self._resolve_zip(location_input)
        else:
            city, state_abbrev = self._parse_city_state(location_input)

        state_slug = self.STATE_ABBREV_TO_SLUG[state_abbrev.upper()]
        city_slug = slugify(city)  # "Los Angeles" → "los-angeles"

        # Validate URL exists
        url = f"{BASE_URL}/{state_slug}/{city_slug}/"
        is_valid = await self._validate_url(url)

        if not is_valid:
            # Fallback: try fetching state page and fuzzy-matching city
            city_slug = await self._fuzzy_match_city(state_slug, city)

        return state_slug, city_slug

    def _is_zipcode(self, s: str) -> bool:
        return bool(re.match(r'^\d{5}(-\d{4})?$', s))

    def _resolve_zip(self, zipcode: str) -> tuple[str, str]:
        from uszipcode import SearchEngine
        search = SearchEngine()
        result = search.by_zipcode(zipcode)
        if not result or not result.major_city:
            raise ValueError(f"Cannot resolve ZIP code: {zipcode}")
        return result.major_city, result.state

    def _parse_city_state(self, s: str) -> tuple[str, str]:
        """Parse 'Los Angeles, CA' or 'Los Angeles, California'."""
        parts = [p.strip() for p in s.split(",")]
        if len(parts) != 2:
            raise ValueError(f"Expected 'City, State' format, got: {s}")
        city, state = parts
        # Handle full state names → abbreviation lookup
        if len(state) > 2:
            state = self._full_state_to_abbrev(state)
        return city, state

    async def _fuzzy_match_city(self, state_slug: str, city: str) -> str:
        """Fetch state page, extract all city slugs, find closest match."""
        html = await fetch(f"{BASE_URL}/{state_slug}/")
        available_cities = parse_city_links(html)  # list of slugs
        target = slugify(city)
        # Exact match first
        if target in available_cities:
            return target
        # Fuzzy match (Levenshtein distance)
        from difflib import get_close_matches
        matches = get_close_matches(target, available_cities, n=1, cutoff=0.7)
        if matches:
            return matches[0]
        raise ValueError(f"City '{city}' not found in {state_slug}")
```

### 6.4 Module: `phases/phase2_discovery.py` — Practice Area Discovery

```python
class PracticeAreaDiscovery:
    """Fetch the city page and extract all available practice area slugs."""

    async def discover(self, state_slug: str, city_slug: str) -> list[str]:
        url = f"{BASE_URL}/{state_slug}/{city_slug}/"
        html = await self.client.fetch(url)
        soup = BeautifulSoup(html, 'lxml')

        practice_areas = []
        # Practice area links follow pattern: /{pa-slug}/{state}/{city}/
        for a in soup.select('a[href]'):
            href = a['href']
            match = re.match(
                rf'^/([\w-]+)/{state_slug}/{city_slug}/?$', href
            )
            if match:
                pa_slug = match.group(1)
                # Exclude navigation slugs that aren't practice areas
                if pa_slug not in ('search', 'advanced_search'):
                    practice_areas.append(pa_slug)

        log.info(f"Discovered {len(practice_areas)} practice areas for "
                 f"{state_slug}/{city_slug}")
        return sorted(set(practice_areas))
```

### 6.5 Module: `phases/phase3_listings.py` — Listing Crawler + Pre-Fill

```python
class ListingCrawler:
    """Crawl all listing pages for a city, extract profile URLs + partial data."""

    async def crawl(
        self,
        state_slug: str,
        city_slug: str,
        practice_areas: list[str]
    ) -> dict[str, AttorneyRecord]:
        """
        Returns: {uuid: partial_AttorneyRecord} with 7 pre-filled fields
        """
        results: dict[str, AttorneyRecord] = {}

        for i, pa in enumerate(practice_areas):
            log.info(f"[{i+1}/{len(practice_areas)}] Crawling: {pa}")
            page = 1

            while page <= MAX_PAGES_PER_CATEGORY:
                url = f"{BASE_URL}/{pa}/{state_slug}/{city_slug}/?page={page}"
                html = await self.client.fetch(url)

                if html is None:
                    break

                cards = self._parse_listing_cards(html)

                if not cards:
                    break  # No more results — end pagination

                new_count = 0
                for card in cards:
                    if card.uuid not in results:
                        results[card.uuid] = card
                        new_count += 1

                log.info(f"  Page {page}: {len(cards)} cards, "
                         f"{new_count} new (total unique: {len(results)})")
                page += 1

        log.info(f"Listing crawl complete: {len(results)} unique attorneys")
        return results

    def _parse_listing_cards(self, html: str) -> list[AttorneyRecord]:
        """Extract partial AttorneyRecord from each attorney card on listing page."""
        soup = BeautifulSoup(html, 'lxml')
        records = []

        # Each attorney card has a profile link containing the UUID
        for link in soup.find_all('a', href=PROFILE_URL_PATTERN):
            href = link['href']
            uuid = self._extract_uuid(href)
            clean_url = self._strip_tracking_params(href)

            record = AttorneyRecord(uuid=uuid, profile_url=clean_url)

            # Extract name from h2 > a
            h2 = link.find_parent('div')  # Card container
            if h2:
                name_el = h2.select_one('h2')
                if name_el:
                    record.name = name_el.get_text(strip=True)

                # Firm name
                firm_el = h2.select_one('a[href*="/lawfirm/"]')
                if firm_el:
                    record.firm_name = firm_el.get_text(strip=True)

                # Phone
                phone_el = h2.select_one('a[href^="tel:"]')
                if phone_el:
                    record.phone = phone_el['href'].replace('tel:+1', '')

                # Selection type (badge text)
                if 'Rising Stars' in h2.get_text():
                    record.selection_type = "Rising Stars"
                elif 'Super Lawyers' in h2.get_text():
                    record.selection_type = "Super Lawyers"

            records.append(record)

        return records

    def _extract_uuid(self, url: str) -> str:
        match = re.search(r'/([\w-]{36})\.html', url)
        return match.group(1) if match else ""

    def _strip_tracking_params(self, url: str) -> str:
        return url.split('?')[0]
```

### 6.6 Module: `phases/phase4_profiles.py` — Profile Deep Scraper

```python
class ProfileScraper:
    """Fetch and parse individual attorney profile pages."""

    async def scrape_all(
        self,
        partial_records: dict[str, AttorneyRecord]
    ) -> list[AttorneyRecord]:
        """Scrape all profile pages with bounded concurrency."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        results = []
        total = len(partial_records)

        tasks = [
            self._scrape_one(semaphore, uuid, partial, i, total)
            for i, (uuid, partial) in enumerate(partial_records.items())
        ]

        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for result in completed:
            if isinstance(result, AttorneyRecord):
                results.append(result)
            elif isinstance(result, Exception):
                log.error(f"Profile scrape failed: {result}")

        return results

    async def _scrape_one(
        self,
        sem: asyncio.Semaphore,
        uuid: str,
        partial: AttorneyRecord,
        index: int,
        total: int
    ) -> AttorneyRecord:
        async with sem:
            await self.rate_limiter.acquire()
            log.info(f"[{index+1}/{total}] Scraping profile: {partial.name}")

            html = await self.client.fetch(partial.profile_url)

            if html is None:
                log.warning(f"Failed to fetch profile for {partial.name}")
                partial.profile_tier = "basic"  # Fallback to listing data
                return partial

            parser = ProfileParser(html, partial.profile_url)
            record = parser.parse()

            # Merge: profile data takes priority, listing data fills gaps
            record = self._merge(profile=record, listing=partial)

            # Infer profile tier
            record.profile_tier = record.infer_profile_tier()

            # Set metadata
            record.scraped_at = datetime.now(timezone.utc).isoformat()

            return record

    def _merge(self, profile: AttorneyRecord, listing: AttorneyRecord) -> AttorneyRecord:
        """Profile data wins. Listing data fills empty fields."""
        for f in fields(AttorneyRecord):
            profile_val = getattr(profile, f.name)
            listing_val = getattr(listing, f.name)
            if not profile_val and listing_val:
                setattr(profile, f.name, listing_val)
        return profile
```

### 6.7 Module: `parsers/profile_parser.py` — Full 33-Field Extraction

```python
class ProfileParser:
    """Parse a single profile page HTML into an AttorneyRecord."""

    def __init__(self, html: str, url: str):
        self.soup = BeautifulSoup(html, 'lxml')
        self.url = url
        self.text = self.soup.get_text()

    def parse(self) -> AttorneyRecord:
        r = AttorneyRecord()

        # ── Group A: Identity ──
        r.uuid = self._extract_uuid()
        r.name = self._safe(self._extract_name)
        r.firm_name = self._safe(self._extract_firm_name)
        r.selection_type = self._safe(self._extract_selection_type)
        r.selection_years = self._safe(self._extract_selection_years)

        # ── Group B: Location ──
        addr = self._safe(self._extract_address, default={})
        r.street = addr.get('street', '')
        r.city = addr.get('city', '')
        r.state = addr.get('state', '')
        r.zip_code = addr.get('zip_code', '')
        r.country = "United States"
        r.geo_coordinates = self._safe(self._extract_geo_coordinates)

        # ── Group C: Contact ──
        r.phone = self._safe(self._extract_phone)
        r.email = self._safe(self._extract_email)
        r.firm_website_url = self._safe(self._extract_firm_website)
        r.professional_webpage_url = self._safe(self._extract_professional_webpage)

        # ── Group D: Professional ──
        r.about = self._safe(self._extract_about)
        r.practice_areas = self._safe(self._extract_practice_areas)
        r.focus_areas = self._safe(self._extract_focus_areas)
        r.licensed_since = self._safe(self._extract_licensed_since)
        r.education = self._safe(self._extract_education)
        r.languages = self._safe(self._extract_languages)

        # ── Group E: Achievements ──
        r.honors = self._safe(self._extract_section, "Honors")
        r.bar_activity = self._safe(self._extract_section, "Bar / Professional Activity")
        r.pro_bono = self._safe(self._extract_section, "Pro bono / Community Service")
        r.publications = self._safe(self._extract_section, "Scholarly Lectures / Writings")

        # ── Group F: Social ──
        socials = self._safe(self._extract_social_links, default={})
        r.linkedin_url = socials.get('linkedin', '')
        r.facebook_url = socials.get('facebook', '')
        r.twitter_url = socials.get('twitter', '')
        r.findlaw_url = socials.get('findlaw', '')

        # ── Group G: Metadata ──
        r.profile_url = self.url.split('?')[0]

        return r

    # ──── Extraction Methods ────

    def _extract_uuid(self) -> str:
        match = re.search(r'/([\w-]{36})\.html', self.url)
        return match.group(1) if match else ""

    def _extract_name(self) -> str:
        h1 = self.soup.select_one('h1')
        return h1.get_text(strip=True) if h1 else ""

    def _extract_firm_name(self) -> str:
        # Firm link near top of profile
        firm_link = self.soup.select_one('a[href*="/lawfirm/"]')
        return firm_link.get_text(strip=True) if firm_link else ""

    def _extract_phone(self) -> str:
        tel = self.soup.select_one('a[href^="tel:"]')
        if tel:
            raw = tel['href'].replace('tel:', '').replace('+1', '')
            return raw.lstrip('1') if raw.startswith('1') else raw
        return ""

    def _extract_email(self) -> str:
        # Check for mailto: links
        mailto = self.soup.select_one('a[href^="mailto:"]')
        if mailto:
            return mailto['href'].replace('mailto:', '')
        # Fallback: regex scan of About text
        about = self._extract_about()
        match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', about)
        return match.group(0) if match else ""

    def _extract_address(self) -> dict:
        """Parse the office location block into components."""
        # Find heading "Office location for ..."
        heading = self.soup.find(string=re.compile(r'Office location for'))
        if not heading:
            return {}

        # The address block follows the heading
        parent = heading.find_parent()
        if not parent:
            return {}

        # Get the text block — lines separated by <br> or newlines
        # Typical format:
        #   9777 Wilshire Blvd.
        #   Suite 517
        #   Beverly Hills, CA 90212
        addr_text = parent.get_text(separator='\n', strip=True)
        lines = [l.strip() for l in addr_text.split('\n') if l.strip()]

        # Remove the heading itself
        lines = [l for l in lines if 'Office location' not in l
                 and 'Phone:' not in l]

        if not lines:
            return {}

        result = {}

        # Last line contains "City, ST ZIP"
        last_line = lines[-1]
        csz_match = re.match(r'^(.+),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$', last_line)
        if csz_match:
            result['city'] = csz_match.group(1).strip()
            result['state'] = csz_match.group(2).strip()
            result['zip_code'] = csz_match.group(3).strip()
            # Everything before last line = street
            result['street'] = ', '.join(lines[:-1])
        else:
            # Fallback: put everything in street
            result['street'] = ', '.join(lines)

        return result

    def _extract_geo_coordinates(self) -> str:
        """Extract lat/lng from Google Maps static image URL."""
        maps_img = self.soup.select_one('img[src*="maps.googleapis.com"]')
        if maps_img:
            match = re.search(r'center=([-\d.]+),([-\d.]+)', maps_img['src'])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        # Fallback: check Maps link href
        maps_link = self.soup.select_one('a[href*="google.com/maps"]')
        if maps_link:
            match = re.search(r'@([-\d.]+),([-\d.]+)', maps_link['href'])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        return ""

    def _extract_about(self) -> str:
        """Extract the narrative bio from the About section."""
        # About text is typically in paragraphs after the header area
        # and before the "Practice areas" heading
        about_parts = []
        in_about = False

        for el in self.soup.find_all(['p', 'h3', 'h2']):
            if el.name in ('h3', 'h2'):
                text = el.get_text(strip=True)
                if 'About' in text:
                    in_about = True
                    continue
                elif in_about and text in (
                    'Practice areas', 'Focus areas', 'Achievements',
                    'Map', 'Office location'
                ):
                    break
            elif in_about and el.name == 'p':
                about_parts.append(el.get_text(strip=True))

        # Fallback: look for paragraphs between header and first h3
        if not about_parts:
            # Grab all <p> tags between the contact info and first section heading
            for p in self.soup.find_all('p'):
                text = p.get_text(strip=True)
                if len(text) > 100 and 'Super Lawyers' not in text:
                    about_parts.append(text)
                if len(about_parts) >= 5:
                    break

        return '\n\n'.join(about_parts)

    def _extract_practice_areas(self) -> str:
        h3 = self.soup.find('h3', string=re.compile(r'^Practice areas$'))
        if h3:
            next_el = h3.find_next_sibling()
            if next_el:
                return next_el.get_text(strip=True)
        # Fallback: header summary "Practice areas: X, Y, Z"
        match = re.search(r'Practice areas?:\s*(.+?)(?:;|$)', self.text)
        return match.group(1).strip() if match else ""

    def _extract_focus_areas(self) -> str:
        h3 = self.soup.find('h3', string=re.compile(r'^Focus areas$'))
        if h3:
            next_el = h3.find_next_sibling()
            if next_el:
                items = next_el.get_text(strip=True)
                return items.replace(', ', MULTIVALUE_DELIMITER)
        return ""

    def _extract_licensed_since(self) -> str:
        match = re.search(
            r'(?:Licensed in|First Admitted:)\s*(.+?)(?:\n|$)',
            self.text
        )
        return match.group(1).strip() if match else ""

    def _extract_education(self) -> str:
        # Look for link to lawschools.superlawyers.com
        edu_link = self.soup.select_one('a[href*="lawschools.superlawyers.com"]')
        if edu_link:
            return edu_link.get_text(strip=True)
        # Fallback: "Educational Background" section
        h3 = self.soup.find('h3', string=re.compile(r'Educational Background'))
        if h3:
            items = []
            for li in h3.find_next('ul', recursive=False) or []:
                items.append(li.get_text(strip=True))
            return MULTIVALUE_DELIMITER.join(items)
        return ""

    def _extract_languages(self) -> str:
        match = re.search(r'Languages?\s+spoken:\s*(.+?)(?:\n|$)', self.text)
        if match:
            langs = match.group(1).strip()
            return langs.replace(', ', MULTIVALUE_DELIMITER)
        return ""

    def _extract_selection_type(self) -> str:
        if 'Selected to Rising Stars' in self.text:
            return "Rising Stars"
        elif 'Selected to Super Lawyers' in self.text:
            return "Super Lawyers"
        return ""

    def _extract_selection_years(self) -> str:
        match = re.search(
            r'Selected to (?:Super Lawyers|Rising Stars):\s*(\d{4}\s*-\s*\d{4})',
            self.text
        )
        return match.group(1).strip() if match else ""

    def _extract_firm_website(self) -> str:
        # "Visit website" link near address
        visit = self.soup.find('a', string=re.compile(r'Visit website'))
        if visit and visit.get('href'):
            return visit['href'].split('?')[0]
        # Fallback: "Find me online" → Website link
        website = self.soup.find('a', string=re.compile(r'^Website'))
        if website and website.get('href'):
            return website['href'].split('?')[0]
        return ""

    def _extract_professional_webpage(self) -> str:
        match = re.search(
            r'Professional Webpage:\s*(https?://\S+)',
            self.text
        )
        return match.group(1).strip() if match else ""

    def _extract_social_links(self) -> dict:
        """Parse 'Find me online' section for social URLs."""
        links = {}
        # Scope to "Find me online" section if possible
        section = self.soup.find(string=re.compile(r'Find me online'))
        search_scope = section.find_parent() if section else self.soup

        for a in search_scope.find_all('a', href=True):
            href = a['href']
            href_lower = href.lower()
            if 'linkedin.com' in href_lower and 'linkedin' not in links:
                links['linkedin'] = href
            elif 'facebook.com' in href_lower and 'facebook' not in links:
                links['facebook'] = href
            elif ('twitter.com' in href_lower or 'x.com' in href_lower) \
                    and 'twitter' not in links:
                links['twitter'] = href
            elif 'findlaw.com' in href_lower and 'findlaw' not in links:
                links['findlaw'] = href

        return links

    def _extract_section(self, heading_text: str) -> str:
        """Extract a list section (Honors, Bar Activity, etc.) as delimited string."""
        h3 = self.soup.find('h3', string=re.compile(re.escape(heading_text)))
        if not h3:
            return ""
        items = []
        # Walk siblings until next <h3> or <hr>
        for sib in h3.find_next_siblings():
            if sib.name in ('h3', 'hr'):
                break
            if sib.name == 'ul':
                for li in sib.find_all('li'):
                    items.append(li.get_text(strip=True))
            elif sib.name == 'li':
                items.append(sib.get_text(strip=True))

        return MULTIVALUE_DELIMITER.join(items)

    # ──── Safety Wrapper ────

    def _safe(self, func, *args, default=""):
        """Wrap extraction in try/except — one bad field can't kill the record."""
        try:
            result = func(*args) if args else func()
            return result if result else default
        except Exception as e:
            log.warning(f"Extraction error in {func.__name__}: {e}")
            return default
```

### 6.8 Module: `parsers/address_parser.py` — Dedicated Address Parsing

```python
class AddressParser:
    """Robust parser for SuperLawyers address blocks.

    Handles formats:
        9777 Wilshire Blvd.       →  street: "9777 Wilshire Blvd., Suite 517"
        Suite 517                     city: "Beverly Hills"
        Beverly Hills, CA 90212       state: "CA"
                                      zip_code: "90212"

        10100 Santa Monica Blvd.  →  street: "10100 Santa Monica Blvd., Suite 2200"
        Suite 2200                    city: "Los Angeles"
        Los Angeles, CA 90067         state: "CA"
                                      zip_code: "90067"
    """

    CSZ_PATTERN = re.compile(
        r'^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$'
    )

    def parse(self, raw_text: str) -> dict:
        lines = [l.strip() for l in raw_text.strip().split('\n') if l.strip()]
        lines = [l for l in lines if not l.startswith('Phone:')
                 and 'Office location' not in l]

        if not lines:
            return {'street': '', 'city': '', 'state': '', 'zip_code': ''}

        # Try to match City, ST ZIP on the last line
        csz_match = self.CSZ_PATTERN.match(lines[-1])
        if csz_match:
            return {
                'street': ', '.join(lines[:-1]),
                'city': csz_match.group(1).strip(),
                'state': csz_match.group(2),
                'zip_code': csz_match.group(3),
            }

        # Fallback: try second-to-last line
        if len(lines) >= 2:
            csz_match = self.CSZ_PATTERN.match(lines[-2])
            if csz_match:
                return {
                    'street': ', '.join(lines[:-2]),
                    'city': csz_match.group(1).strip(),
                    'state': csz_match.group(2),
                    'zip_code': csz_match.group(3),
                }

        # Last resort: everything in street
        return {
            'street': ', '.join(lines),
            'city': '', 'state': '', 'zip_code': ''
        }
```

### 6.9 Module: `phases/phase5_export.py` — Multi-Format Export

```python
class Exporter:
    """Export AttorneyRecords to CSV, JSONL, and optionally SQLite."""

    def export_csv(self, records: list[AttorneyRecord], filepath: str):
        """Primary export — CSV with UTF-8 BOM for Excel compatibility."""
        with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)
            writer.writerow(AttorneyRecord.csv_headers())
            for record in records:
                row = record.to_csv_row()
                # Truncate oversized cells
                row = [
                    v[:MAX_CELL_LENGTH] + '... [truncated]'
                    if len(v) > MAX_CELL_LENGTH else v
                    for v in row
                ]
                writer.writerow(row)
        log.info(f"CSV exported: {filepath} ({len(records)} records)")

    def export_jsonl(self, records: list[AttorneyRecord], filepath: str):
        """One JSON object per line — for programmatic consumption."""
        with open(filepath, 'w', encoding='utf-8') as f:
            for record in records:
                json.dump(record.to_dict(), f, ensure_ascii=False)
                f.write('\n')
        log.info(f"JSONL exported: {filepath} ({len(records)} records)")

    def export_sqlite(self, records: list[AttorneyRecord], filepath: str):
        """SQLite database with indexes for common queries."""
        import sqlite3
        conn = sqlite3.connect(filepath)
        cols = AttorneyRecord.csv_headers()
        col_defs = ', '.join(f'"{c}" TEXT' for c in cols)
        conn.execute(f'CREATE TABLE IF NOT EXISTS attorneys ({col_defs})')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_uuid ON attorneys(uuid)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_city_state ON attorneys(city, state)')

        placeholders = ', '.join('?' * len(cols))
        for record in records:
            conn.execute(
                f'INSERT OR REPLACE INTO attorneys VALUES ({placeholders})',
                record.to_csv_row()
            )
        conn.commit()
        conn.close()
        log.info(f"SQLite exported: {filepath} ({len(records)} records)")
```

---

## 7. Anti-Detection & Resilience Strategy

### 7.1 Request Fingerprint Diversification

| Technique | Implementation |
|-----------|---------------|
| **User-Agent Rotation** | Pool of 15+ real browser UAs, rotated per request |
| **Header Realism** | Full browser-like header set (Accept, Encoding, Sec-Fetch-*) |
| **Referer Chain** | Set Referer to logical parent page (listing → profile) |
| **Request Ordering** | Mimic human browsing: city page → listing → profile (not random) |
| **Session Rotation** | New httpx client every ~50 requests |
| **Cookie Handling** | Accept and re-send cookies within sessions |

### 7.2 Rate Limiting

```python
class TokenBucketRateLimiter:
    """Enforces minimum delay + max requests per minute."""

    def __init__(self, min_delay=1.0, max_delay=3.0, max_per_minute=30):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_per_minute = max_per_minute
        self.request_times = []

    async def acquire(self):
        # Random jitter delay
        delay = random.uniform(self.min_delay, self.max_delay)
        await asyncio.sleep(delay)

        # Rolling minute window enforcement
        now = time.monotonic()
        self.request_times = [t for t in self.request_times if now - t < 60]
        if len(self.request_times) >= self.max_per_minute:
            wait = 60 - (now - self.request_times[0])
            log.info(f"Rate limit: waiting {wait:.1f}s")
            await asyncio.sleep(wait)
        self.request_times.append(time.monotonic())
```

### 7.3 Request Headers Template

```python
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
```

### 7.4 Proxy Support (Optional)

```python
# For large-scale runs or if IP-based blocking is detected
PROXY_LIST = [...]
# Rotate proxies round-robin, or sticky per session
```

---

## 8. Error Handling & Recovery

### 8.1 HTTP Error Handling

| Status Code | Action |
|-------------|--------|
| 200 | Process normally |
| 301/302 | Follow redirect (httpx auto-follows) |
| 403 | Rotate UA + wait 30s + retry (max 3x) → skip on failure |
| 404 | Log warning, skip this URL, continue |
| 429 | Exponential backoff: 60s → 120s → 300s |
| 500/502/503 | Retry with backoff (max 3x), then skip |
| Connection Error | Retry with 10s delay (max 3x) |
| Timeout (30s) | Retry once, then skip |

### 8.2 Parsing Error Isolation

Every field extraction is wrapped in `_safe()` — one broken field cannot crash the record or the entire crawl. The `ProfileParser._safe()` method catches all exceptions, logs a warning, and returns the default value.

### 8.3 Checkpoint / Resume System

```python
@dataclass
class CrawlCheckpoint:
    """Saved to disk every CHECKPOINT_INTERVAL profiles."""
    location: str                          # "california/los-angeles"
    completed_practice_areas: list[str]    # Already fully crawled
    current_practice_area: str             # Currently in progress
    current_page: int                      # Last completed page
    scraped_uuids: list[str]              # All UUIDs already processed
    records_saved: int                     # Total records in output file
    timestamp: str                         # ISO 8601

    def save(self, filepath: str = CHECKPOINT_FILE):
        with open(filepath, 'w') as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, filepath: str = CHECKPOINT_FILE) -> 'CrawlCheckpoint':
        with open(filepath) as f:
            return cls(**json.load(f))
```

**Resume flow:**
1. On start, check if `checkpoint.json` exists
2. If yes, load it, skip completed practice areas, start from `current_page`
3. Pre-populate `seen_uuids` from checkpoint
4. Append to existing CSV instead of overwriting

### 8.4 Logging

```python
import logging

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Levels used:
# INFO     — Progress: "Scraping profile 142/3500: John Smith"
# WARNING  — Non-fatal: "Empty 'about' field for uuid abc123"
# ERROR    — Failures: "HTTP 403 on profile page after 3 retries"
# DEBUG    — Verbose: full HTML dumps for troubleshooting
```

---

## 9. Data Pipeline & Output

### 9.1 Data Cleaning Rules

| Field | Cleaning Rule |
|-------|---------------|
| `name` | Strip whitespace, normalize unicode, remove prefix "Attorney" |
| `street` | Normalize whitespace, join multi-line with ", " |
| `city` | Title case, strip whitespace |
| `state` | Uppercase 2-letter abbreviation |
| `zip_code` | Validate `\d{5}(-\d{4})?`, strip non-matching |
| `phone` | Strip `+1`, format as `XXX-XXX-XXXX` |
| `email` | Lowercase, validate `x@y.z` pattern |
| `about` | Strip excessive whitespace/newlines, truncate at 10K chars |
| `practice_areas` | Normalize separator to `" ; "`, deduplicate, sort |
| `focus_areas` | Same as practice_areas |
| `languages` | Normalize separator to `" ; "` |
| `honors`, `bar_activity`, `pro_bono`, `publications` | Normalize separator to `" ; "` |
| All URLs | Strip `?adSubId=`, `?fli=`, tracking params; validate `https?://` |
| `geo_coordinates` | Validate `[-]?\d+\.\d+,[-]?\d+\.\d+` |
| `profile_tier` | Must be `basic` \| `expanded` \| `premium` |

### 9.2 Auto-Generated Bio Handling

```python
BOILERPLATE_PATTERNS = [
    r"^[\w\s.]+ is an attorney who represents clients in the",
    r"Being selected to Super Lawyers is limited to a small number",
    r"passed the bar exam and was admitted to legal practice in",
    r"is recognized by peers and was selected to",
]

def clean_about(text: str) -> str:
    """If the about text is auto-generated boilerplate, return empty string."""
    if any(re.search(p, text) for p in BOILERPLATE_PATTERNS):
        return ""  # Don't store machine-generated bios
    return text.strip()
```

### 9.3 Output File Naming

```
superlawyers_{city}_{state}_{YYYYMMDD_HHMMSS}.csv
superlawyers_{city}_{state}_{YYYYMMDD_HHMMSS}.jsonl   (optional)
superlawyers_{city}_{state}_{YYYYMMDD_HHMMSS}.db      (optional)
```

### 9.4 Sample CSV Output (33 columns)

```csv
"uuid","name","firm_name","selection_type","selection_years","description","street","city","state","zip_code","country","geo_coordinates","phone","email","firm_website_url","professional_webpage_url","about","practice_areas","focus_areas","licensed_since","education","languages","honors","bar_activity","pro_bono","publications","linkedin_url","facebook_url","twitter_url","findlaw_url","profile_url","profile_tier","scraped_at"
"e926cca4-e9fb-497f-b993-c937da341408","Nadira T. Imam","Law Offices of Lawrence H. Jacobson","Rising Stars","2020 - 2026","Super Lawyer with multiple years experience...","9777 Wilshire Blvd., Suite 517","Beverly Hills","CA","90212","United States","34.067070,-118.397720","310-271-0747","","https://www.lawrencejacobson.com","https://www.lawrencejacobson.com/attorney/nadira-imam/","Attorney Nadira T. Imam is a senior associate...","Business/Corporate ; Real Estate: Business ; Estate Planning & Probate","Business Formation and Planning ; Contracts ; Estate Planning ; LLCs ; Trusts ; Wills","2017, California","Abraham Lincoln University School of Law","English ; German ; Spanish","Lawrence J. Blake Award, BHBA (2015) ; Board of Governors Award, BHBA (2018)","President, Beverly Hills Bar Association (2024) ; President Elect, BHBA (2023)","Roxbury Park Legal Clinic (2018) ; Samoshel Homeless Shelter (2015)","Speaker: Mental Health and Substance Abuse... (BHBA, 2023)","https://www.linkedin.com/in/nadira-imam-334329b5/","","","https://lawyers.findlaw.com/california/beverly-hills/nadira-imam-NTEyNTI3MF8x/","https://profiles.superlawyers.com/california/beverly-hills/lawyer/nadira-t-imam/e926cca4-e9fb-497f-b993-c937da341408.html","premium","2026-02-10T14:30:22Z"
```

---

## 10. Critical Review & Gap Analysis

### 10.1 Identified Gaps & Mitigations

| # | Gap | Severity | Mitigation |
|---|-----|----------|------------|
| 1 | **Email addresses not publicly listed** | 🟡 Medium | Accept empty `email`; regex-scan bio text as fallback |
| 2 | **403 on some paths** | 🔴 High | Realistic headers, UA rotation, delays, session rotation |
| 3 | **Basic profiles have minimal data** (5-7 fields only) | 🟡 Medium | Graceful empty fields + `profile_tier` column signals quality |
| 4 | **"Serving X, based in Y" listings** | 🟡 Medium | Capture all attorneys from listings; address comes from profile |
| 5 | **ZIP → wrong city mapping** | 🟡 Medium | Fuzzy matching against state city list; user confirmation prompt |
| 6 | **No pagination "total pages" indicator** | 🟡 Medium | Detect empty results / no-results text / duplicate page content |
| 7 | **Auto-generated bios on basic profiles** | 🟡 Medium | Detect via regex patterns; store empty `about` for boilerplate |
| 8 | **Profile URL tracking params** (`?adSubId`) | 🟢 Low | Strip query params; UUID-based dedup ignores them |
| 9 | **Rate limiting / IP blocking** | 🔴 High | Checkpoint/resume, proxy rotation, conservative 30 req/min |
| 10 | **Character encoding (names with accents)** | 🟢 Low | UTF-8-BOM encoding; BS4 handles encoding automatically |
| 11 | **Large cities: 10,000+ attorneys** | 🟡 Medium | Checkpoint support, progress logging, ETA display |
| 12 | **Sponsored cards appear twice on same page** | 🟢 Low | UUID-based dedup handles transparently |
| 13 | **Practice area pages with 0 results** | 🟢 Low | Empty-result detection → skip gracefully |
| 14 | **Some profiles link to firm pages not lawyer pages** | 🟢 Low | URL filter: only process `/lawyer/` URLs, skip `/lawfirm/` |

### 10.2 Edge Cases

| Edge Case | Handler |
|-----------|---------|
| No profile page (listing-only attorney) | Skip; pre-fill data retained as fallback |
| `/lawfirm/` URL instead of `/lawyer/` | URL regex filter excludes firm profiles |
| Sponsored card duplicates in listing | UUID set dedup |
| Practice area with 0 attorneys | Empty result → break pagination loop |
| City names with special chars ("St. Louis", "Corona de Tucson") | `python-slugify` + fuzzy match fallback |
| Multi-office attorney (multiple addresses) | Take the first office location shown |
| Profile page returns 403 after 3 retries | Log error, keep pre-fill listing data, mark `profile_tier = "basic"` |
| Extremely long bio (>10K chars) | Truncate with `... [truncated]` suffix |
| Attorney with both SL and RS designations | Take the most recent/prominent one |

### 10.3 Legal & Ethical Considerations

- All scraped data is **publicly available** on the website
- Respect `robots.txt` directives when accessible
- Rate limit to ≤30 req/min to avoid impacting site performance
- Do not bypass authentication or access private data
- Do not scrape contact form submissions or login-protected content
- Include 1-3 second delays between requests

---

## 11. Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|:----------:|:------:|------------|
| IP blocked after N requests | Medium | High | Proxy rotation, conservative rate limiting, checkpoint/resume |
| Site structure / DOM changes | Low | High | Modular parsers with fallback selectors, fixture-based tests |
| Incomplete data on basic profiles | High | Low | `profile_tier` column + empty-field tolerance |
| ZIP code → wrong city mapping | Medium | Medium | Fuzzy matching, state-page city list, user confirmation |
| Anti-bot CAPTCHA introduced | Low | Critical | CAPTCHA detection → alert user → suggest manual step |
| Very large result set (>10K) | Medium | Medium | Checkpoint system, progress bars, ETA calculation |
| Network timeouts / instability | Medium | Low | Retry with exponential backoff |
| Encoding issues in names | Low | Low | UTF-8-BOM, explicit encoding handling |
| Auto-generated bio stored as real | High | Low | Boilerplate regex detection → empty `about` |
| Geo coordinates missing (no Maps embed) | Low | Low | Graceful empty `geo_coordinates` |

---


## 12. Appendices

### Appendix A: CSS Selector / Regex Reference (Profile Pages)

```
FIELD                   SELECTOR / STRATEGY
────────────────────────────────────────────────────────────────
uuid                    URL regex: /([\w-]{36})\.html
name                    h1 (first on page)
firm_name               a[href*="/lawfirm/"] (first occurrence)
phone                   a[href^="tel:"] → strip "tel:+1"
selection_type          Text match: "Selected to Rising Stars" vs "Super Lawyers"
selection_years         Regex: r'Selected to (?:SL|RS):\s*(\d{4}\s*-\s*\d{4})'
street/city/state/zip   "Office location for" heading → sibling text block → CSZ regex
geo_coordinates         img[src*="maps.googleapis.com"] → center=LAT,LNG
firm_website_url        a with text "Visit website" → href
professional_webpage    Regex: r'Professional Webpage:\s*(https?://\S+)'
about                   Paragraphs between "About" tab and first h3 section
practice_areas          h3 text="Practice areas" → next sibling text
focus_areas             h3 text="Focus areas" → next sibling text
licensed_since          Regex: r'(?:Licensed in|First Admitted:)\s*(.+?)(?:\n|$)'
education               a[href*="lawschools.superlawyers.com"] text
languages               Regex: r'Languages?\s+spoken:\s*(.+?)(?:\n|$)'
honors                  h3 text="Honors" → following <ul> items
bar_activity            h3 text="Bar / Professional Activity" → following items
pro_bono                h3 text="Pro bono / Community Service" → following items
publications            h3 text="Scholarly Lectures / Writings" → following items
linkedin_url            "Find me online" section → a[href*="linkedin.com"]
facebook_url            "Find me online" section → a[href*="facebook.com"]
twitter_url             "Find me online" section → a[href*="twitter.com" or "x.com"]
findlaw_url             "Additional sources" section → a[href*="findlaw.com"]
profile_tier            Inferred: has bar_activity/pro_bono → premium; has phone+real bio → expanded; else basic
```

### Appendix B: Full State Slug List (51)

```
alabama, alaska, arizona, arkansas, california, colorado,
connecticut, delaware, florida, georgia, hawaii, idaho,
illinois, indiana, iowa, kansas, kentucky, louisiana,
maine, maryland, massachusetts, michigan, minnesota,
mississippi, missouri, montana, nebraska, nevada,
new-hampshire, new-jersey, new-mexico, new-york,
north-carolina, north-dakota, ohio, oklahoma, oregon,
pennsylvania, rhode-island, south-carolina, south-dakota,
tennessee, texas, utah, vermont, virginia, washington,
washington-dc, west-virginia, wisconsin, wyoming
```

### Appendix C: Practice Area Slugs (~130)

Extracted from the Los Angeles city page. Full list discovered dynamically at runtime during Phase 2.

```
administrative-law, admiralty-and-maritime-law, adoption,
agriculture-law, alternative-dispute-resolution, animal-bites,
animal-law, antitrust-litigation, appellate, asbestos,
assault-and-battery, auto-dealer-fraud, aviation-and-aerospace,
aviation-accidents-plaintiff, bad-faith-insurance, banking,
bankruptcy, birth-injury, brain-injury, business-and-corporate,
business-litigation, business-organizations, cannabis-law,
motor-vehicle-accidents, child-support, civil-litigation,
civil-rights, class-action-and-mass-torts, closely-held-business,
collections, communications, constitutional-law,
construction-accident, construction-defects, construction-litigation,
consumer-law, contracts, credit-repair, creditor-debtor-rights,
criminal-defense, custody-and-visitation, dui-dwi, defamation,
disability, discrimination, divorce, domestic-violence,
drug-and-alcohol-violations, ...
(full list dynamically extracted per city during Phase 2)
```

### Appendix D: CLI Usage (Planned)

```bash
# Basic usage
python main.py --location "Los Angeles, CA"
python main.py --location "90210"
python main.py --location "Chicago, IL" --format csv

# Advanced options
python main.py --location "New York, NY" \
    --format csv,jsonl,sqlite \
    --output ./my_output \
    --max-concurrent 3 \
    --delay-min 2.0 \
    --delay-max 5.0 \
    --resume                    # Resume from checkpoint

# Dry run (count attorneys without scraping profiles)
python main.py --location "Houston, TX" --dry-run
```

---

*Plan Version: 2.0 | Date: 2026-02-10 | 33-column data model | 5-phase pipeline*
