# config.py
"""All configuration constants for the scraper."""

import os

# Proxy (set PROXY_URL env var to enable, e.g. "http://user:pass@host:port")
PROXY_URL = os.environ.get("PROXY_URL")

# URLs
BASE_URL = "https://attorneys.superlawyers.com"
PROFILE_BASE_URL = "https://profiles.superlawyers.com"

# Rate limiting
DELAY_MIN = 2.0                # seconds between requests
DELAY_MAX = 5.0
MAX_CONCURRENT = 3             # simultaneous profile fetches
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0      # exponential: 2s -> 4s -> 8s

# Pagination
MAX_PAGES_PER_CATEGORY = 200   # safety limit per practice area

# Output
OUTPUT_DIR = "./output"
DATA_DIR = "./data"
CSV_ENCODING = "utf-8-sig"
MULTIVALUE_DELIMITER = " ; "
MAX_CELL_LENGTH = 10_000

# Anti-detection
STEALTH_MODE = True
BROWSER_PROFILE_DIR = "./data/.browser_profile"
DELAY_BEFORE_RETURN = 2.0     # seconds, let JS challenges resolve

# Timeouts
REQUEST_TIMEOUT = 30           # seconds
