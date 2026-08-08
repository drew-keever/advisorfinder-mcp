from advisorfinder_mcp import server


def _envelope_keys_present(result):
    assert "data_as_of" in result
    assert result["advisorfinder"]["link"] == "https://advisorfinder.com"
    assert "verify" in result
    assert "coverage_caveats" in result


def test_search_by_primary_name():
    result = server.search_firms(name="alpha wealth")
    _envelope_keys_present(result)
    crds = {r["crd"] for r in result["results"]}
    assert "100001" in crds


def test_other_name_only_match_carries_matched_as():
    result = server.search_firms(name="gateway")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "100002"
    assert "matched_as" in row
    assert "Gateway Capital Partners" in row["matched_as"]


def test_primary_match_has_no_matched_as():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "matched_as" not in row


def test_state_firm_carries_caveat():
    result = server.search_firms(name="delta state")
    assert result["result_count"] == 1
    row = result["results"][0]
    assert row["crd"] == "500001"
    assert "caveat" in row
    assert "state-registered" in row["caveat"].lower()


def test_sec_firm_has_no_state_caveat():
    result = server.search_firms(name="alpha wealth")
    row = next(r for r in result["results"] if r["crd"] == "100001")
    assert "caveat" not in row


def test_state_filter_by_address_state():
    result = server.search_firms(state="MA")
    crds = {r["crd"] for r in result["results"]}
    assert "100002" in crds  # BETA is in MA
    assert "100001" not in crds  # ALPHA is in NY


def test_no_results_has_guidance():
    result = server.search_firms(name="zzznomatchzzz")
    assert result["result_count"] == 0
    assert "not_found_guidance" in result
    assert "iapd" in result["verify"] and "brokercheck" in result["verify"]


def test_verify_populated_even_with_results():
    result = server.search_firms(name="alpha wealth")
    assert result["result_count"] > 0
    assert "adviserinfo.sec.gov" in result["verify"]["iapd"]


def test_aum_band_field_present_on_every_result():
    result = server.search_firms(state="NY")
    for row in result["results"]:
        assert "aum_band" in row


def test_search_firms_unsanitizable_name_errors_instead_of_browse():
    result = server.search_firms(name="!!!")
    assert "error" in result
    assert not result.get("results")


def test_search_firms_renders_llc_uppercase():
    result = server.search_firms(name="alpha wealth")
    names = {r["name"] for r in result["results"]}
    assert "Alpha Wealth LLC" in names
