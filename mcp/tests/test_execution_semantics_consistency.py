"""Guards the exact class of drift found and fixed in dmr/semantics.py's
fnb.revenue.ly_mtd entry: measures.py (the execution layer) had the real
curated DAX measure name ([FNB: Revenue (LY MTD)]), while semantics.py still
declared the old, never-existed name ([FNB: Revenue (Last Year Month)]) that
400'd every live get_dmr_fnb_performance call.

This does NOT test labels or aggregation types (test_columns_semantic_consistency.py
already covers additivity/valueKind/semanticType agreement) - it tests the one
thing that actually caused a live outage: does the literal DAX measure string
a SUPPORTED semantic entry claims to use actually exist as a real,
executable measure definition in measures.py.
"""

from dmr.measures import MEASURE_DEFINITIONS
from dmr.semantics import SEMANTICS

# The full set of DAX measure expressions the execution layer can actually
# run - anything a SUPPORTED semantic entry names that ISN'T in here is
# exactly the drift class this test exists to catch.
_EXECUTABLE_MEASURE_EXPRESSIONS = {dax_expression for _alias, dax_expression in MEASURE_DEFINITIONS.values()}


def test_every_supported_metrics_curated_measure_is_a_real_executable_measure():
    """A SUPPORTED semantic entry's curated_measure must be a DAX expression
    that genuinely exists in measures.py's MEASURE_DEFINITIONS - never a
    name that merely sounds plausible. This is the exact check that would
    have failed on the fnb.revenue.ly_mtd drift before it was fixed."""
    drifted = []
    for key, entry in SEMANTICS.items():
        if entry.queryability != "SUPPORTED":
            continue
        if entry.curated_measure is None:
            continue
        if entry.curated_measure not in _EXECUTABLE_MEASURE_EXPRESSIONS:
            drifted.append((key, entry.curated_measure))
    assert not drifted, f"Semantic entries referencing a measure not in measures.py: {drifted}"


def test_a_deliberately_wrong_curated_measure_string_would_be_caught():
    """Proves the check above has teeth - a semantic entry pointing at a
    measure name that sounds real but isn't in MEASURE_DEFINITIONS must fail
    it, the same way the original fnb.revenue.ly_mtd drift would have."""
    assert "[FNB: Revenue (Last Year Month)]" not in _EXECUTABLE_MEASURE_EXPRESSIONS
    assert "[FNB: Revenue (LY MTD)]" in _EXECUTABLE_MEASURE_EXPRESSIONS


def test_fnb_revenue_ly_mtd_now_agrees_with_the_execution_layer():
    """The specific entry that drifted - locked to the corrected value so a
    future regression back to the old broken name fails immediately, not
    just generically via the loop-based test above."""
    from dmr.measures import MEASURE_DEFINITIONS as _MD
    from dmr.reports import Measure

    entry = SEMANTICS["fnb.revenue.ly_mtd"]
    _alias, real_dax_expression = _MD[Measure.FNB_REVENUE_LAST_YEAR_MONTH]
    assert entry.curated_measure == real_dax_expression
