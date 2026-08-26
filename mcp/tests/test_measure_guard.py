"""measure_guard.find_filter_removing_measures() is currently blocked on
getting real measure DAX text out of the live model (see that module's
docstring - the earlier `INFO.VIEW.MEASURES()` scan was a false negative,
not a real verification). This tests the DETECTOR LOGIC ITSELF against
synthetic Name/Expression data - proving the check would actually catch a
filter-removing measure once it's wired up to real expression text. It does
NOT claim the live model has been checked - that's still an open item.
"""

from dmr.measure_guard import find_filter_removing_measures


def test_flags_each_known_filter_removing_function():
    rows = [
        {"Name": "Uses ALL", "Expression": "CALCULATE([Revenue], ALL(Dates))"},
        {"Name": "Uses ALLEXCEPT", "Expression": "CALCULATE([Revenue], ALLEXCEPT(T, T[Hotel_ID]))"},
        {"Name": "Uses ALLSELECTED", "Expression": "CALCULATE([Revenue], ALLSELECTED())"},
        {"Name": "Uses REMOVEFILTERS", "Expression": "CALCULATE([Revenue], REMOVEFILTERS(Hotels))"},
        {"Name": "Clean", "Expression": "SUM(Sales[Amount])"},
    ]

    hits = find_filter_removing_measures(rows)

    hit_names = {hit["Name"] for hit in hits}
    assert hit_names == {"Uses ALL", "Uses ALLEXCEPT", "Uses ALLSELECTED", "Uses REMOVEFILTERS"}


def test_accepts_the_bracketed_dax_result_row_shape():
    rows = [{"[Name]": "Uses ALL", "[Expression]": "CALCULATE([Revenue], ALL(Dates))"}]
    hits = find_filter_removing_measures(rows)
    assert len(hits) == 1


def test_allowlisted_measure_is_not_flagged():
    from dmr import measure_guard

    measure_guard.ALLOWLIST["Reviewed Exception"] = "reviewed and safe - test fixture"
    try:
        rows = [{"Name": "Reviewed Exception", "Expression": "CALCULATE([Revenue], ALL(Dates))"}]
        hits = find_filter_removing_measures(rows)
        assert hits == []
    finally:
        del measure_guard.ALLOWLIST["Reviewed Exception"]


def test_blank_expression_never_false_positives():
    """Guards against exactly the false-negative failure mode this module's
    docstring documents (INFO.VIEW.MEASURES() returning a blank
    [Expression] for every row) - a blank expression must never be flagged,
    since flagging it would be just as meaningless as the false "0 hits"
    the earlier scan produced."""
    rows = [{"Name": "Whatever", "Expression": ""}]
    assert find_filter_removing_measures(rows) == []
