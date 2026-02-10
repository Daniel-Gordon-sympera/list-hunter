# tests/test_export.py
import os
import csv
import json
import tempfile
from commands.export import clean_record, run
from models import AttorneyRecord


def test_clean_phone_formats():
    record = AttorneyRecord(phone="+13102710747")
    cleaned = clean_record(record)
    assert cleaned.phone == "310-271-0747"


def test_clean_phone_already_formatted():
    record = AttorneyRecord(phone="310-271-0747")
    cleaned = clean_record(record)
    assert cleaned.phone == "310-271-0747"


def test_clean_strips_url_tracking():
    record = AttorneyRecord(
        firm_website_url="https://example.com?adSubId=123&fli=456",
        linkedin_url="https://linkedin.com/in/test?trk=abc",
    )
    cleaned = clean_record(record)
    assert cleaned.firm_website_url == "https://example.com"
    assert "trk" not in cleaned.linkedin_url


def test_clean_auto_bio_removed():
    record = AttorneyRecord(
        about="John Smith is an attorney who represents clients in the area of business law."
    )
    cleaned = clean_record(record)
    assert cleaned.about == ""


def test_clean_real_bio_kept():
    bio = "Jane has over 20 years of experience in corporate law."
    record = AttorneyRecord(about=bio)
    cleaned = clean_record(record)
    assert cleaned.about == bio


def test_clean_state_uppercase():
    record = AttorneyRecord(state="ca")
    cleaned = clean_record(record)
    assert cleaned.state == "CA"


def test_clean_truncates_long_cell():
    record = AttorneyRecord(about="x" * 15_000)
    cleaned = clean_record(record)
    assert len(cleaned.about) <= 10_015  # 10000 + "... [truncated]"
    assert cleaned.about.endswith("... [truncated]")


def test_export_produces_csv(tmp_path):
    records_data = [
        AttorneyRecord(uuid="test-1", name="Test Attorney", city="LA", state="CA").to_dict()
    ]
    records_path = tmp_path / "records.json"
    with open(records_path, "w") as f:
        json.dump(records_data, f)

    output_dir = tmp_path / "output"
    csv_path = run(str(records_path), str(output_dir))

    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert len(headers) == 33
        row = next(reader)
        assert row[0] == "test-1"
        assert row[1] == "Test Attorney"
