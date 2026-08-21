"""Tiny shared date helpers - Fabric returns date/datetime columns as
ISO-8601 strings like "2026-08-15T00:00:00"; the contract only ever shows
the date portion (granularity is always "day" for the columns that use this
today)."""

from datetime import date, datetime


def parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date()


def date_str(value) -> str:
    return parse_date(value).isoformat()
