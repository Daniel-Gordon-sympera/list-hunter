"""Parse individual attorney profile pages into full AttorneyRecord objects."""

import re
import logging
from bs4 import BeautifulSoup, NavigableString
from models import AttorneyRecord
from parsers.address_parser import parse_address
import config

log = logging.getLogger(__name__)

UUID_PATTERN = re.compile(r"/([\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12})\.html")


def parse_profile(html: str, url: str) -> AttorneyRecord:
    """Parse a profile page HTML into an AttorneyRecord with all 33 fields."""
    parser = _ProfileParser(html, url)
    return parser.parse()


class _ProfileParser:
    def __init__(self, html: str, url: str):
        self.soup = BeautifulSoup(html, "lxml")
        self.url = url
        self.text = self.soup.get_text()

    def parse(self) -> AttorneyRecord:
        r = AttorneyRecord()

        # Group A: Identity
        r.uuid = self._extract_uuid()
        r.name = self._safe(self._extract_name)
        r.firm_name = self._safe(self._extract_firm_name)
        r.selection_type = self._safe(self._extract_selection_type)
        r.selection_years = self._safe(self._extract_selection_years)
        r.description = self._safe(self._extract_description)

        # Group B: Location
        addr = self._safe(self._extract_address, default={})
        r.street = addr.get("street", "")
        r.city = addr.get("city", "")
        r.state = addr.get("state", "")
        r.zip_code = addr.get("zip_code", "")
        r.country = "United States"
        r.geo_coordinates = self._safe(self._extract_geo_coordinates)

        # Group C: Contact
        r.phone = self._safe(self._extract_phone)
        r.email = self._safe(self._extract_email)
        r.firm_website_url = self._safe(self._extract_firm_website)
        r.professional_webpage_url = self._safe(self._extract_professional_webpage)

        # Group D: Professional
        r.about = self._safe(self._extract_about)
        r.practice_areas = self._safe(self._extract_practice_areas)
        r.focus_areas = self._safe(self._extract_focus_areas)
        r.licensed_since = self._safe(self._extract_licensed_since)
        r.education = self._safe(self._extract_education)
        r.languages = self._safe(self._extract_languages)

        # Group E: Achievements
        r.honors = self._safe(self._extract_section, "Honors")
        r.bar_activity = self._safe(self._extract_section, "Bar / Professional Activity")
        r.pro_bono = self._safe(self._extract_section, "Pro bono / Community Service")
        r.publications = self._safe(self._extract_section, "Scholarly Lectures / Writings")

        # Group F: Social
        socials = self._safe(self._extract_social_links, default={})
        r.linkedin_url = socials.get("linkedin", "")
        r.facebook_url = socials.get("facebook", "")
        r.twitter_url = socials.get("twitter", "")
        r.findlaw_url = socials.get("findlaw", "")

        # Group G: Metadata
        r.profile_url = self.url.split("?")[0]
        r.profile_tier = r.infer_profile_tier()

        return r

    def _extract_uuid(self) -> str:
        match = UUID_PATTERN.search(self.url)
        return match.group(1) if match else ""

    def _extract_name(self) -> str:
        h1 = self.soup.select_one("h1#attorney_name")
        if h1:
            return h1.get_text(strip=True)
        h1 = self.soup.select_one("h1")
        return h1.get_text(strip=True) if h1 else ""

    def _extract_description(self) -> str:
        h2 = self.soup.select_one("h2.paragraph-large")
        return h2.get_text(strip=True) if h2 else ""

    def _extract_firm_name(self) -> str:
        firm_link = self.soup.select_one('a[href*="/lawfirm/"]')
        return firm_link.get_text(strip=True) if firm_link else ""

    def _extract_phone(self) -> str:
        tel = self.soup.select_one('a[href^="tel:"]')
        if tel:
            return tel.get_text(strip=True)
        return ""

    def _extract_email(self) -> str:
        mailto = self.soup.select_one('a[href^="mailto:"]')
        if mailto:
            return mailto["href"].replace("mailto:", "").split("?")[0]
        return ""

    def _extract_address(self) -> dict:
        # Look for the "Office location for" heading in the map tab or card
        h3_addr = self.soup.find("h3", string=re.compile(r"Office location for"))
        if not h3_addr:
            return {}
        parent_div = h3_addr.find_parent("div")
        if not parent_div:
            return {}
        addr_text = parent_div.get_text(separator="\n", strip=True)
        return parse_address(addr_text)

    def _extract_geo_coordinates(self) -> str:
        maps_img = self.soup.select_one('img[src*="maps.googleapis.com"]')
        if maps_img:
            match = re.search(r"center=([-\d.]+),([-\d.]+)", maps_img["src"])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        # Fallback: look for google maps link
        maps_link = self.soup.select_one('a[href*="google.com/maps"]')
        if maps_link:
            match = re.search(r"@([-\d.]+),([-\d.]+)", maps_link["href"])
            if match:
                return f"{match.group(1)},{match.group(2)}"
        return ""

    def _extract_about(self) -> str:
        # Primary: div#about tab pane contains the bio paragraphs
        about_div = self.soup.find("div", id="about")
        if about_div:
            paragraphs = []
            for p in about_div.find_all("p"):
                text = p.get_text(strip=True)
                if text:
                    paragraphs.append(text)
            if paragraphs:
                return "\n\n".join(paragraphs)
        return ""

    def _extract_practice_areas(self) -> str:
        # In the practice-areas tab, practice areas text is a NavigableString
        # directly after the <h3>Practice areas</h3>
        pa_div = self.soup.find("div", id="practice-areas")
        scope = pa_div if pa_div else self.soup

        h3 = scope.find("h3", string=re.compile(r"^Practice areas?$", re.I))
        if h3:
            # The text is a NavigableString (bare text node) after the h3
            ns = h3.next_sibling
            if isinstance(ns, NavigableString):
                text = ns.strip()
                if text:
                    return text
            # Fallback: check next element sibling
            next_el = h3.find_next_sibling()
            if next_el and next_el.name != "h3":
                return next_el.get_text(strip=True)

        # Fallback: sidebar practice areas
        for p in self.soup.find_all("p", class_="mb-0"):
            text = p.get_text(strip=True)
            if text.startswith("Practice areas:"):
                raw = text.replace("Practice areas:", "").strip()
                # Remove "view more" suffix
                raw = re.sub(r";?\s*view more$", "", raw)
                return raw

        return ""

    def _extract_focus_areas(self) -> str:
        pa_div = self.soup.find("div", id="practice-areas")
        scope = pa_div if pa_div else self.soup

        h3 = scope.find("h3", string=re.compile(r"^Focus areas?$", re.I))
        if h3:
            # Focus areas are in a <p> after the h3
            next_el = h3.find_next_sibling()
            if next_el and next_el.name == "p":
                return next_el.get_text(strip=True)
            # Could also be a NavigableString
            ns = h3.next_sibling
            if isinstance(ns, NavigableString):
                text = ns.strip()
                if text:
                    return text
        return ""

    def _extract_licensed_since(self) -> str:
        # Primary: sidebar "Licensed in <state> since:<year>"
        for p in self.soup.find_all("p", class_="mb-0"):
            text = p.get_text(strip=True)
            match = re.search(r"Licensed in \w+ since:\s*(\d{4})", text)
            if match:
                return match.group(1)

        # Fallback: achievements tab "First Admitted: <year>, <state>"
        ach_div = self.soup.find("div", id="achievements")
        if ach_div:
            fa_text = ach_div.get_text(separator=" ", strip=True)
            match = re.search(r"First Admitted:\s*(\d{4})", fa_text)
            if match:
                return match.group(1)

        # Fallback: raw text
        match = re.search(r"First Admitted:\s*(\d{4})", self.text)
        if match:
            return match.group(1)

        return ""

    def _extract_education(self) -> str:
        # Primary: link to lawschools.superlawyers.com
        edu_link = self.soup.select_one('a[href*="lawschools.superlawyers.com"]')
        if edu_link:
            text = edu_link.get_text(strip=True)
            # Filter out generic "Law schools" link
            if text and text.lower() != "law schools":
                return text

        # Fallback: sidebar "Education:<school name>"
        for p in self.soup.find_all("p", class_="mb-0"):
            text = p.get_text(strip=True)
            if text.startswith("Education:"):
                return text.replace("Education:", "").strip()

        return ""

    def _extract_languages(self) -> str:
        match = re.search(r"Languages?\s+spoken:\s*(.+?)(?:\n|$)", self.text)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_selection_type(self) -> str:
        # Primary: italic span with "Selected to ..."
        for span in self.soup.find_all("span", class_="fst-italic"):
            text = span.get_text(strip=True)
            if "Selected to Rising Stars" in text:
                return "Rising Stars"
            if "Selected to Super Lawyers" in text:
                return "Super Lawyers"

        # Fallback: raw text
        if "Selected to Rising Stars" in self.text:
            return "Rising Stars"
        if "Selected to Super Lawyers" in self.text:
            return "Super Lawyers"
        return ""

    def _extract_selection_years(self) -> str:
        # Primary: italic span with "Selected to <type>: <years>"
        for span in self.soup.find_all("span", class_="fst-italic"):
            text = span.get_text(strip=True)
            match = re.search(
                r"Selected to (?:Super Lawyers|Rising Stars):\s*(.+)", text
            )
            if match:
                return match.group(1).strip()

        # Fallback: raw text
        match = re.search(
            r"Selected to (?:Super Lawyers|Rising Stars):\s*(.+?)(?:\s{2,}|\n|$)",
            self.text,
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_firm_website(self) -> str:
        visit = self.soup.find("a", string=re.compile(r"Visit website", re.I))
        if visit and visit.get("href"):
            return visit["href"].split("?")[0]
        return ""

    def _extract_professional_webpage(self) -> str:
        # Look in achievements tab for "Professional Webpage:" label
        ach_div = self.soup.find("div", id="achievements")
        scope = ach_div if ach_div else self.soup

        pw_span = scope.find("span", string=re.compile(r"Professional Webpage"))
        if pw_span:
            # The link is a sibling or within the parent container
            parent = pw_span.find_parent()
            if parent:
                link = parent.find_next("a", href=True)
                if link:
                    return link["href"].split("?")[0]

        # Fallback: regex in raw text
        match = re.search(r"Professional Webpage:\s*(https?://\S+)", self.text)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_social_links(self) -> dict:
        links = {}

        # Determine scope: prefer "Find me online" section, fallback to full page
        scope = self.soup
        fmo = self.soup.find(string=re.compile(r"Find me online"))
        if fmo:
            container = fmo.find_parent()
            if container:
                scope = container.find_parent() if container.name == "h2" else container

        all_links = scope.find_all("a", href=True)

        for a in all_links:
            href = a["href"]
            hl = href.lower()
            # Personal LinkedIn (linkedin.com/in/) is preferred over company
            if "linkedin.com/in/" in hl:
                links["linkedin"] = href
            elif "linkedin.com/company/" in hl and "linkedin" not in links:
                links["linkedin"] = href
            elif "facebook.com" in hl and "facebook" not in links:
                if "superlawyers" not in hl:
                    links["facebook"] = href
            elif ("twitter.com" in hl or "x.com" in hl) and "twitter" not in links:
                if "superlawyers" not in hl:
                    links["twitter"] = href
            elif "lawyers.findlaw.com" in hl and "findlaw" not in links:
                links["findlaw"] = href

        return links

    def _extract_section(self, heading_text: str) -> str:
        """Extract content following an h3 heading, joining list items."""
        h3 = self.soup.find("h3", string=re.compile(re.escape(heading_text), re.I))
        if not h3:
            return ""
        items = []
        for sib in h3.find_next_siblings():
            if sib.name in ("h3", "hr"):
                break
            if sib.name == "ul":
                for li in sib.find_all("li"):
                    text = li.get_text(strip=True)
                    if text:
                        items.append(text)
            elif sib.name == "li":
                text = sib.get_text(strip=True)
                if text:
                    items.append(text)
            elif sib.name == "p":
                text = sib.get_text(strip=True)
                if text:
                    items.append(text)
        return config.MULTIVALUE_DELIMITER.join(items)

    def _safe(self, func, *args, default=""):
        """Call a function safely, returning a default on any exception."""
        try:
            result = func(*args) if args else func()
            return result if result is not None else default
        except Exception as e:
            func_name = getattr(func, "__name__", str(func))
            log.warning(f"Extraction error in {func_name}: {e}")
            return default
