# Super Lawyers Scraper

Scrapes attorney profiles from the Super Lawyers directory.
Accepts a city/state location and produces a CSV with 33 data fields per attorney.

## Quick Start

### Prerequisites
- Python 3.11+

### Install

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

### Scrape a city (full pipeline)

```bash
python cli.py discover "Los Angeles, CA"
python cli.py crawl-listings data/los-angeles_ca/practice_areas.json
python cli.py fetch-profiles data/los-angeles_ca/listings.json
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

Output: `output/superlawyers_los-angeles_ca_YYYYMMDD_HHMMSS.csv`

## Commands

| Command | Input | Output |
|---------|-------|--------|
| discover | "City, ST" | practice_areas.json |
| crawl-listings | practice_areas.json | listings.json |
| fetch-profiles | listings.json | html/{uuid}.html files |
| parse-profiles | html/ dir + listings.json | records.json |
| export | records.json | .csv file |

## Re-parsing without re-fetching

If you need to fix a parser selector, you don't need to re-download anything:

```bash
python cli.py parse-profiles data/los-angeles_ca/
python cli.py export data/los-angeles_ca/records.json
```

## Running tests

```bash
pytest tests/ -v
```
