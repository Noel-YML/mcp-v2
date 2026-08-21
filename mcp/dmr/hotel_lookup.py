"""Resolves a hotel's DISPLAY metadata (name, country-derived currency) from
its verified `hotel_id` - never the other way around, and never trusting
anything a caller supplies. Used only for the analytics contract's
`scope.hotelDisplayName`/`context.currency` (Phase 3) - this has nothing to
do with authorization, which is scope_token.py's job alone.

Does its own small, direct `_Hotels` lookup through the SAME
`IFabricQueryService` already passed into the calling tool - no separate
Fabric wiring, and it inherits all of Phase 2's hardening (pooling,
retries, response caps) for free.

Currency comes from `_Hotels[Country]` (real, populated data - confirmed
against the live model) via a small mapping. There is deliberately NO
fallback to a configured default currency: an unmapped country returns
`currency=None, currency_source="unknown"` rather than guessing - a
silently-wrong currency (e.g. labeling real SGD figures as AUD for a future
non-Australian property) is worse than an honest unknown. Timezone is not
derivable from anything in this model today (Country="Australia" alone
doesn't distinguish Sydney, which observes daylight saving, from Brisbane,
which doesn't) - `timezone`/`timezone_source` are always `None`/"unknown".

Fails soft: a lookup failure (Fabric error, no matching hotel) returns
`UNKNOWN_HOTEL_METADATA`, not an exception - a missing display name is a
`quality.warnings` entry on an otherwise-complete result, never a reason to
fail the whole tool call. See tools/dmr_tools.py.
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

from fabric_client.service import IFabricQueryService

CACHE_TTL_SECONDS = 600

# Real, populated _Hotels[Country] values -> ISO currency code. Extend as
# the hotel portfolio grows; there is intentionally no catch-all default.
_COUNTRY_CURRENCY = {
    "Australia": "AUD",
}


@dataclass(frozen=True)
class HotelMetadata:
    display_name: Optional[str]
    country: Optional[str]
    currency: Optional[str]
    currency_source: str  # "country_mapping" | "unknown"
    timezone: Optional[str] = None
    timezone_source: str = "unknown"


UNKNOWN_HOTEL_METADATA = HotelMetadata(
    display_name=None, country=None, currency=None, currency_source="unknown"
)


def _build_metadata(display_name: Optional[str], country: Optional[str]) -> HotelMetadata:
    country = country.strip() if isinstance(country, str) else country
    currency = _COUNTRY_CURRENCY.get(country) if country else None
    return HotelMetadata(
        display_name=display_name,
        country=country,
        currency=currency,
        currency_source="country_mapping" if currency else "unknown",
    )


class HotelLookup:
    """One instance per process (module-scoped, like `FabricQueryService`
    and `health.Readiness`) so the cache is actually shared across calls."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict[int, tuple[float, HotelMetadata]] = {}

    def resolve(self, service: IFabricQueryService, hotel_id: int) -> HotelMetadata:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(hotel_id)
            if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
                return cached[1]

        metadata = self._resolve_uncached(service, hotel_id)

        with self._lock:
            self._cache[hotel_id] = (now, metadata)
        return metadata

    def _resolve_uncached(self, service: IFabricQueryService, hotel_id: int) -> HotelMetadata:
        query = f"""
        EVALUATE
        SELECTCOLUMNS(
            FILTER(_Hotels, [Hotel_ID] = {int(hotel_id)}),
            "Hotel_Name", [Hotel_Name],
            "Country", [Country]
        )
        """
        try:
            result = service.run_query(query)
        except Exception:  # noqa: BLE001 - a cosmetic lookup must never raise into the caller
            return UNKNOWN_HOTEL_METADATA

        if result.error is not None or not result.rows:
            return UNKNOWN_HOTEL_METADATA

        row = result.rows[0]
        display_name = row.get("[Hotel_Name]", row.get("Hotel_Name"))
        country = row.get("[Country]", row.get("Country"))
        return _build_metadata(display_name, country)


_default_lookup = HotelLookup()


def resolve(service: IFabricQueryService, hotel_id: int) -> HotelMetadata:
    return _default_lookup.resolve(service, hotel_id)
