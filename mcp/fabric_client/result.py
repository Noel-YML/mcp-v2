"""Result of a Fabric DAX query: either rows, or an error message - never both."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FabricQueryResult:
    rows: Optional[list] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, rows: list) -> "FabricQueryResult":
        return cls(rows=rows)

    @classmethod
    def failed(cls, error: str) -> "FabricQueryResult":
        return cls(error=error)
