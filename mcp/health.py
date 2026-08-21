"""Liveness/readiness checks shared by both hostings (function_app.py and
server.py). Deliberately minimal responses - no tenant/client/dataset IDs,
no environment-variable values, no package versions, no exception text, no
internal network information. See tools/diagnostics.py's removal: this
replaces `echo` as the operational "is it alive" check, but it is not an
AI-callable tool in either hosting - it's a plain HTTP endpoint.

Three tiers (function_app.py exposes all three, server.py only the first
two - see that module for why):
  - liveness(): the process is running. No dependency checks at all.
  - Readiness(): settings parsed, scope keys loaded, AND a real credential
    token acquisition succeeds - not just "the settings exist," which would
    have reported ready even with a managed identity that isn't actually
    attached or doesn't have the Fabric role assignment. The result is
    cached for READINESS_CACHE_SECONDS so repeated probes don't hammer
    Microsoft Entra ID.
  - deep_check(): one real, minimal Fabric query - a genuine round-trip,
    not cached, and never anonymous (function_app.py gates it behind a
    function key) since it costs a real Fabric call.
"""

import threading
import time
from dataclasses import dataclass
from typing import Optional

from fabric_client.service import FabricQueryService

READINESS_CACHE_SECONDS = 30

# A trivial, single-row query - just proves the credential + workspace/dataset
# combination can execute a query at all. Not one of the four DMR reports,
# and never hotel-scoped (nothing here should ever return report data).
_DEEP_CHECK_QUERY = "EVALUATE { 1 }"


def liveness() -> dict:
    return {"status": "live"}


class Readiness:
    """One instance per process (module-scoped, like FabricQueryService
    itself) so the cache is actually shared across requests.
    """

    def __init__(self, fabric_service: FabricQueryService, public_keys: dict[str, str]):
        self._fabric_service = fabric_service
        self._public_keys = public_keys
        self._lock = threading.Lock()
        self._cached_at: float = 0.0
        self._cached_ready: bool = False

    def check(self) -> bool:
        now = time.monotonic()
        with self._lock:
            if now - self._cached_at < READINESS_CACHE_SECONDS:
                return self._cached_ready
        ready = self._check_uncached()
        with self._lock:
            self._cached_at = now
            self._cached_ready = ready
        return ready

    def _check_uncached(self) -> bool:
        if not self._public_keys:
            return False
        try:
            self._fabric_service.check_credential()
        except Exception:
            return False
        return True


@dataclass(frozen=True)
class DeepCheckResult:
    ok: bool


def deep_check(fabric_service: FabricQueryService) -> DeepCheckResult:
    result = fabric_service.run_query(_DEEP_CHECK_QUERY)
    return DeepCheckResult(ok=result.error is None)
