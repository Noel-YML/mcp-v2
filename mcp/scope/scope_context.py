"""Frozen, verified hotel-authorization context.

Built ONLY by scope_token.resolve_scope (never constructed ad hoc from a raw
argument or header value) after independent signature and claim
verification. Deliberately has no `hotel_name` field - a display name is
not an authorization fact, and a tool that genuinely needs one resolves it
separately through a trusted lookup, not through this object. See the
Phase 1 "Protect the data" plan for the fuller rationale.

`permissions` and `session_id` are placeholder-quality until Phase 1E's JWT
claims land (today's token only carries `hotel_id` + `exp`) - every scope
currently gets the uniform `{"dmr:read"}` grant and an empty session_id.
The shape is final; the values it's populated from will tighten in 1E.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ScopeContext:
    hotel_id: int
    session_id: str
    permissions: frozenset[str]
    expires_at: datetime
