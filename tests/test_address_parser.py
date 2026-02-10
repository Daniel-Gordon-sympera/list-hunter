from parsers.address_parser import parse_address


def test_standard_address():
    raw = "9777 Wilshire Blvd.\nSuite 517\nBeverly Hills, CA 90212"
    result = parse_address(raw)
    assert result["street"] == "9777 Wilshire Blvd., Suite 517"
    assert result["city"] == "Beverly Hills"
    assert result["state"] == "CA"
    assert result["zip_code"] == "90212"


def test_single_line_street():
    raw = "100 Main Street\nNew York, NY 10001"
    result = parse_address(raw)
    assert result["street"] == "100 Main Street"
    assert result["city"] == "New York"
    assert result["state"] == "NY"
    assert result["zip_code"] == "10001"


def test_zip_plus_four():
    raw = "500 Broadway\nSan Francisco, CA 94133-1234"
    result = parse_address(raw)
    assert result["zip_code"] == "94133-1234"


def test_strips_phone_and_heading():
    raw = "Office location for John Smith\n100 Main St\nPhone: 555-1234\nBoston, MA 02101"
    result = parse_address(raw)
    assert result["street"] == "100 Main St"
    assert result["city"] == "Boston"
    assert "Phone" not in result["street"]
    assert "Office location" not in result["street"]


def test_empty_input():
    result = parse_address("")
    assert result["street"] == ""
    assert result["city"] == ""
    assert result["state"] == ""
    assert result["zip_code"] == ""


def test_no_csz_match():
    raw = "Some Unknown Format\nNo City State Zip Here"
    result = parse_address(raw)
    assert result["street"] == "Some Unknown Format, No City State Zip Here"
    assert result["city"] == ""
