# models.py
"""AttorneyRecord dataclass — 33 fields across 7 groups."""

from dataclasses import dataclass, field, fields, asdict
from datetime import datetime, timezone
import re


@dataclass
class AttorneyRecord:
    """Complete attorney data model — 33 fields across 7 groups."""

    # Group A: Identity
    uuid: str = ""
    name: str = ""
    firm_name: str = ""
    selection_type: str = ""
    selection_years: str = ""
    description: str = ""

    # Group B: Location
    street: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    country: str = "United States"
    geo_coordinates: str = ""

    # Group C: Contact
    phone: str = ""
    email: str = ""
    firm_website_url: str = ""
    professional_webpage_url: str = ""

    # Group D: Professional Profile
    about: str = ""
    practice_areas: str = ""
    focus_areas: str = ""
    licensed_since: str = ""
    education: str = ""
    languages: str = ""

    # Group E: Achievements
    honors: str = ""
    bar_activity: str = ""
    pro_bono: str = ""
    publications: str = ""

    # Group F: Social & Web
    linkedin_url: str = ""
    facebook_url: str = ""
    twitter_url: str = ""
    findlaw_url: str = ""

    # Group G: Metadata
    profile_url: str = ""
    profile_tier: str = ""
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @classmethod
    def csv_headers(cls) -> list[str]:
        return [f.name for f in fields(cls)]

    def to_csv_row(self) -> list[str]:
        return [getattr(self, f.name) for f in fields(self)]

    def to_dict(self) -> dict:
        return asdict(self)

    def completeness_score(self) -> float:
        filled = sum(1 for f in fields(self) if getattr(self, f.name))
        return round(filled / len(fields(self)), 2)

    def infer_profile_tier(self) -> str:
        if self.bar_activity or self.pro_bono or self.publications:
            return "premium"
        elif self.phone and self.about and not self._is_auto_bio():
            return "expanded"
        return "basic"

    def _is_auto_bio(self) -> bool:
        patterns = [
            r"^[\w\s.]+ is an attorney who represents clients in the",
            r"Being selected to Super Lawyers is limited to a small number",
            r"passed the bar exam and was admitted to legal practice in",
        ]
        return any(re.search(p, self.about) for p in patterns)
