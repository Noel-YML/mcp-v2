"""Builds the DAX query text for the ARIEL product tools. Given the same
inputs, always produces the same query string with no side effects - which
is what makes this easy to unit test without touching Fabric.
"""

from typing import Optional


def _escape_hotel_name(hotel_name: str) -> str:
    return hotel_name.replace('"', '""')


def _measure_columns(measures: dict) -> str:
    return ",\n        ".join(f'"{alias}", {expr}' for alias, expr in measures.items())


def build_snapshot_query(measures: dict, hotel_name: Optional[str]) -> str:
    """A single-row snapshot, optionally filtered to one hotel. Shared by
    get_product_automation_metrics (product measures) and
    list_available_products (presence measures) - same shape, different
    measure dict.
    """
    measure_columns = _measure_columns(measures)
    if hotel_name:
        escaped_hotel_name = _escape_hotel_name(hotel_name)
        return f"""
        EVALUATE
        CALCULATETABLE(
            SUMMARIZECOLUMNS(
                {measure_columns}
            ),
            _Hotels[Hotel_Name] = "{escaped_hotel_name}"
        )
        """
    return f"""
    EVALUATE
    SUMMARIZECOLUMNS(
        {measure_columns}
    )
    """


def build_trend_query(measures: dict, hotel_name: Optional[str], months: int) -> str:
    """A month-by-month series, most recent `months` months, oldest first."""
    measure_columns = _measure_columns(measures)
    monthly_table = f"""
        SUMMARIZECOLUMNS(
            _Dates[MonthnYear],
            _Dates[MonthInCalendar],
            {measure_columns}
        )
    """
    if hotel_name:
        escaped_hotel_name = _escape_hotel_name(hotel_name)
        monthly_table = f"""
        CALCULATETABLE(
            {monthly_table},
            _Hotels[Hotel_Name] = "{escaped_hotel_name}"
        )
        """
    return f"""
    EVALUATE
    TOPN({int(months)}, {monthly_table}, _Dates[MonthnYear], 0)
    ORDER BY _Dates[MonthnYear]
    """
