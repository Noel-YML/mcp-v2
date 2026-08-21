"""Automated guard against filter-removing DAX in the measures the DMR
tools depend on.

WHY THIS FILE EXISTS, AND AN HONEST CORRECTION: dax_query_builder.py's dual
`Hotel_ID` filter and dmr_tools.py's result-row verification both operate
on returned ROWS - neither can prove a measure's *aggregate value* was
computed only from the scoped hotel. A future measure using `ALL(`,
`ALLEXCEPT(`, `ALLSELECTED(`, or `REMOVEFILTERS(` inside a CALCULATE could
still show the correct Hotel_ID on a row whose number quietly includes
other hotels' data. This module is meant to be the check that watches for
that, by scanning each measure's DAX `[Expression]` for those functions.

Earlier in this hardening pass, a scan using `INFO.VIEW.MEASURES()` via the
Power BI `executeQueries` REST API reported "0 of 103 measures" use any of
these functions. **That result was a false negative, not a real
verification** - `[Expression]` came back `ISBLANK() = TRUE` for every
single measure checked (confirmed by testing arbitrary, unrelated measures
too, not just the DMR ones), so the `CONTAINSSTRING([Expression], "ALL(")`
style check was comparing against an empty string for every row and could
never have found anything, regardless of what any measure actually
contains. `executeQueries` / `INFO.VIEW.MEASURES()` does not appear to
expose real measure DAX text for this model - getting it likely requires
XMLA/TOM-level metadata access (e.g. via Tabular Editor, SSMS, or an
ADOMD.NET-based client), which this project doesn't currently have wired
up. Until someone runs this guard against real expression text obtained
that way, **treat the filter-removal risk as UNVERIFIED, not "confirmed
clean."**

Any measure that has a genuine reason to use one of these functions must be
added to ALLOWLIST with a comment explaining why it's safe - never let a
hit pass through silently, once real expression data is available to check.

This is a review-time check, not something run per DMR tool call - run
`python measure_guard.py` by hand (piped real Name/Expression data from
whatever tool can actually retrieve it), or wire it into CI, whenever
measures.py or the model's measures change.
"""

import json
import sys

# Measure name -> reason it's allowed to use a filter-removing function
# despite the check below. Empty until a real, reviewed exception exists.
ALLOWLIST: dict[str, str] = {}

FLAGGED_FUNCTIONS = ("ALL(", "ALLEXCEPT(", "ALLSELECTED(", "ALLNOBLANKROW(", "REMOVEFILTERS(")

# Intended to fetch fresh measure definitions, restricted to the measures
# this project actually depends on (the "Matrix v2" family - see
# dmr/measures.py) rather than every measure in the model. CONFIRMED NOT
# TO WORK via the Power BI executeQueries REST API for this model -
# [Expression] comes back blank for every measure through that path (see
# the module docstring). Left here as the intended shape for whatever
# XMLA/TOM-based tool ends up able to retrieve real expression text - do
# not assume this query "works" just because it runs without error.
DISCOVERY_QUERY = """
EVALUATE
SELECTCOLUMNS(
    FILTER(
        INFO.VIEW.MEASURES(),
        CONTAINSSTRING([Name], "Matrix: Value")
            || CONTAINSSTRING([Name], "Segment:")
            || CONTAINSSTRING([Name], "FNB:")
            || CONTAINSSTRING([Name], "Holdings:")
    ),
    "Name", [Name],
    "Expression", [Expression]
)
"""


def find_filter_removing_measures(measures: list[dict]) -> list[dict]:
    """`measures` is a list of {"Name": ..., "Expression": ...} dicts (or
    the DAX-result-row shape {"[Name]": ..., "[Expression]": ...} -
    both are accepted). Returns the ones using a flagged function AND not
    on the allowlist - an empty list means clean.
    """
    hits = []
    for measure in measures:
        name = measure.get("Name", measure.get("[Name]"))
        expression = measure.get("Expression", measure.get("[Expression]")) or ""
        if name in ALLOWLIST:
            continue
        if any(fn in expression for fn in FLAGGED_FUNCTIONS):
            hits.append({"Name": name, "Expression": expression})
    return hits


if __name__ == "__main__":
    # Reads rows (in the shape DISCOVERY_QUERY's results come back as) from
    # stdin as JSON, so this can be piped from whatever DAX-execution tool
    # is at hand without this module needing its own Fabric credentials.
    rows = json.load(sys.stdin)
    hits = find_filter_removing_measures(rows)
    if hits:
        print(f"FAILED: {len(hits)} measure(s) use a filter-removing function and aren't allowlisted:")
        for hit in hits:
            print(f"  - {hit['Name']}: {hit['Expression']}")
        sys.exit(1)
    print(f"OK: {len(rows)} measure(s) checked, none use a filter-removing function outside the allowlist.")
