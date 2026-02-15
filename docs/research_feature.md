# Feature Research: Super Lawyers Scraper Enhancements

## 1. Parallel Practice Area Crawling

### Problem
The sequential crawl-listings phase iterates PAs one at a time. With 100+ PAs at ~3.5s/page, a full city takes 60+ minutes. Most time is spent waiting on network I/O.

### Research: Async Concurrency Patterns

**asyncio.Semaphore for bounded concurrency**: The standard pattern for limiting concurrent async tasks. Workers acquire the semaphore before starting, releasing it when done. This caps active coroutines without blocking the event loop.

**Three-layer concurrency model**:
1. **Worker semaphore** (PA-level): Controls how many PAs crawl simultaneously
2. **HTTP semaphore** (request-level): Already in ScraperClient, limits in-flight requests
3. **Per-request delay**: Random sleep before each request

This layered approach means extra workers overlap their sleep times, improving throughput without increasing actual request pressure.

**asyncio.gather vs TaskGroup**: `gather(*tasks, return_exceptions=True)` is simpler and sufficient. Python 3.11's TaskGroup adds structured concurrency but would cancel all workers on first exception, which isn't desired.

### Design Decisions
- Per-PA output files for natural checkpointing (no shared mutable state)
- Post-crawl merge phase for cross-PA dedup
- `asyncio.Event` for cooperative stop signaling (max_results)

## 2. Practice Area Filter + Max Results

### Problem
Full city crawls are expensive. Developers and users need targeted scraping for specific PAs or quick samples.

### Research: CLI Argument Patterns
- Comma-separated values are the standard for multi-value CLI args
- `--max-results` follows the pagination/limit pattern from APIs
- Early stopping requires cooperative signaling in async contexts

### Design Decisions
- Filter applies before any crawling begins (no wasted work)
- Unknown slugs produce warnings but don't abort
- Max results applied at merge time for exact count (approximate during crawl)

## 3. File Logging

### Problem
Console output is ephemeral. When a long crawl fails, there's no record of what happened. Debug-level messages are suppressed on console but valuable for post-mortem analysis.

### Research: Python Logging Best Practices
- **Root logger at DEBUG, handlers at different levels**: Standard pattern. Root captures everything; handlers filter.
- **Handler accumulation**: `root.handlers.clear()` prevents duplicate handlers when `setup_logging()` is called multiple times.
- **File handler always at DEBUG**: Captures all messages regardless of console verbosity.
- **Timestamped filenames**: Prevents overwriting logs from previous runs.

### Design Decisions
- `log_setup.py` as a dedicated module (not inline in cli.py)
- Per-command log files with timestamps: `{command}_{YYYYMMDD_HHMMSS}.log`
- Console-only for `discover` (data dir doesn't exist yet)
- Log directory: `data/{city}_{st}/logs/`

## 4. Rich Progress Bar

### Problem
Verbose logging during long crawls produces wall-of-text output. Users want a visual progress indicator.

### Research: Terminal Progress Libraries
- **Rich**: Full-featured terminal toolkit. `rich.progress.Progress` supports multiple task bars, spinners, and live updates. Thread-safe and async-compatible.
- **tqdm**: Simpler but less customizable. No built-in support for multiple concurrent task bars.
- **Alive-progress**: Good visuals but less mature ecosystem.

**Rich + logging coexistence**: Rich's `RichHandler` and `Progress` share a `Console` object to prevent output interleaving. Console handler level is raised to WARNING when progress bar is active.

### Design Decisions
- Callback pattern decouples progress display from crawl logic
- `SUPERLAWYERS_NO_PROGRESS=1` env var disables progress (CI, piped output)
- `CrawlProgress`: PA completion bar + attorney counter + active worker status
- `FetchProgress`: Simple completed/total bar
- Progress renders to stderr (stdout reserved for output paths)

## 5. State-of-the-Art Async Scraping Techniques

### Rate Limiting Strategies
- **Token bucket**: Smooth rate limiting over time windows
- **Adaptive delays**: Increase delays on 429/503 responses, decrease on success
- **Request queues**: Priority queues for different request types

### Anti-Detection
- **Browser fingerprint rotation**: Different viewport sizes, WebGL renderers
- **Session management**: Rotate cookies/sessions periodically
- **Request pattern analysis**: Vary navigation patterns to look human

### Resilience Patterns
- **Circuit breaker**: Stop requests after N consecutive failures
- **Bulkhead isolation**: Separate pools for different request types
- **Idempotent operations**: Safe to retry without side effects
