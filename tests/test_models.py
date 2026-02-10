# tests/test_models.py
from models import AttorneyRecord


def test_csv_headers_has_33_columns():
    headers = AttorneyRecord.csv_headers()
    assert len(headers) == 33
    assert headers[0] == "uuid"
    assert headers[-1] == "scraped_at"


def test_to_csv_row_matches_header_count():
    record = AttorneyRecord(uuid="abc-123", name="Jane Doe")
    row = record.to_csv_row()
    assert len(row) == 33
    assert row[0] == "abc-123"
    assert row[1] == "Jane Doe"


def test_completeness_score_empty():
    record = AttorneyRecord()
    # country="United States" and scraped_at are auto-set, so 2/33
    score = record.completeness_score()
    assert score == round(2 / 33, 2)


def test_completeness_score_partial():
    record = AttorneyRecord(uuid="x", name="Y", city="Z")
    # uuid, name, city, country, scraped_at = 5/33
    assert record.completeness_score() == round(5 / 33, 2)


def test_infer_tier_premium():
    record = AttorneyRecord(bar_activity="President, BHBA (2024)")
    assert record.infer_profile_tier() == "premium"


def test_infer_tier_expanded():
    record = AttorneyRecord(
        phone="310-271-0747",
        about="Attorney Jane Doe has practiced law for over 20 years specializing in corporate transactions and real estate matters across Southern California."
    )
    assert record.infer_profile_tier() == "expanded"


def test_infer_tier_basic():
    record = AttorneyRecord(name="John Smith")
    assert record.infer_profile_tier() == "basic"


def test_infer_tier_basic_with_auto_bio():
    record = AttorneyRecord(
        phone="555-1234",
        about="John Smith is an attorney who represents clients in the area of business law."
    )
    assert record.infer_profile_tier() == "basic"


def test_is_auto_bio_detects_boilerplate():
    record = AttorneyRecord(
        about="Being selected to Super Lawyers is limited to a small number of attorneys."
    )
    assert record._is_auto_bio() is True


def test_is_auto_bio_real_bio():
    record = AttorneyRecord(
        about="Jane has over 20 years of experience in corporate law and has handled over 500 mergers."
    )
    assert record._is_auto_bio() is False


def test_to_dict_returns_all_fields():
    record = AttorneyRecord(uuid="test-uuid")
    d = record.to_dict()
    assert d["uuid"] == "test-uuid"
    assert "scraped_at" in d
    assert len(d) == 33


def test_country_defaults_to_us():
    record = AttorneyRecord()
    assert record.country == "United States"
