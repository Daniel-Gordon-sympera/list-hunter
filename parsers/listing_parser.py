"""Parse attorney listing pages into partial AttorneyRecord objects."""

import re
from bs4 import BeautifulSoup
from models import AttorneyRecord

UUID_PATTERN = re.compile(r"/([\w-]{36})\.html")


def parse_listing_page(html: str) -> list[AttorneyRecord]:
    """Extract partial AttorneyRecords from a listing page.

    Each record has up to 7 fields pre-filled:
    uuid, name, firm_name, phone, description, selection_type, profile_url
    """
    soup = BeautifulSoup(html, "lxml")
    records = []
    seen_uuids: set[str] = set()

    for card in soup.find_all("div", class_="serp-container"):
        # Get the name link from h2.full-name
        h2 = card.select_one("h2.full-name")
        if not h2:
            continue

        name_link = h2.select_one("a[href]")
        if not name_link:
            continue

        href = name_link["href"]

        # Only process /lawyer/ URLs, skip /lawfirm/ and /contact/
        if "/lawyer/" not in href:
            continue

        uuid_match = UUID_PATTERN.search(href)
        if not uuid_match:
            continue

        uuid = uuid_match.group(1)
        if uuid in seen_uuids:
            continue
        seen_uuids.add(uuid)

        # Strip tracking query params from profile URL
        clean_url = href.split("?")[0]

        record = AttorneyRecord(uuid=uuid, profile_url=clean_url)

        # Name
        record.name = name_link.get_text(strip=True)

        # Firm name: the a.single-link element holds the firm name
        firm_el = card.select_one("a.single-link")
        if firm_el:
            record.firm_name = firm_el.get_text(strip=True)

        # Phone: extract from tel: link
        phone_el = card.select_one('a[href^="tel:"]')
        if phone_el:
            raw = phone_el["href"].replace("tel:", "").replace("+1", "")
            record.phone = raw.lstrip("1") if raw.startswith("1") else raw

        # Description: the tagline paragraph
        desc_el = card.select_one("p.ts_tagline")
        if desc_el:
            record.description = desc_el.get_text(strip=True)

        # Selection type: check for ribbon icon with aria-label
        ribbon = card.select_one("i.icon-ribbon")
        if ribbon:
            aria_label = ribbon.get("aria-label", "")
            if "Rising Stars" in aria_label:
                record.selection_type = "Rising Stars"
            elif "Super Lawyers" in aria_label:
                record.selection_type = "Super Lawyers"

        records.append(record)

    return records
