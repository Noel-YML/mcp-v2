"""Fabric/Power BI connection settings, read once from environment variables.

The credential fields are optional here (not validated at import time) so the
server can still start - and `echo` still works - without them configured;
FabricQueryService raises only when a Fabric-backed tool is actually called.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FabricOptions:
    tenant_id: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    workspace_id: str
    dataset_id: str

    @classmethod
    def from_env(cls) -> "FabricOptions":
        return cls(
            tenant_id=os.environ.get("AR_FABRIC_TENANT_ID"),
            client_id=os.environ.get("AR_FABRIC_CLIENT_ID"),
            client_secret=os.environ.get("AR_FABRIC_CLIENT_SECRET"),
            workspace_id=os.environ.get("PRODUCT_FABRIC_WORKSPACE_ID", "d03466f9-16a1-4b47-a8cd-20d1975a3088"),
            dataset_id=os.environ.get("PRODUCT_FABRIC_DATASET_ID", "75c6b480-82e9-474c-bda1-8529a0c0d06f"),
        )
