"""Fabric/Power BI query execution.

`IFabricQueryService` is the contract - the same shape as the C# port's
interface - so tools can be tested against a hand-written fake instead of
live Fabric. `FabricQueryService` is the only implementation today.
"""

import logging
from typing import Optional, Protocol

import requests
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import ClientSecretCredential

from config import FabricOptions
from fabric_client.result import FabricQueryResult

logger = logging.getLogger("ariel-mcp-server")

_POWERBI_SCOPE = "https://analysis.windows.net/powerbi/api/.default"


class IFabricQueryService(Protocol):
    def run_product_query(self, dax_query: str) -> FabricQueryResult: ...


class FabricQueryService:
    def __init__(self, options: FabricOptions):
        self._options = options
        self._credential: Optional[ClientSecretCredential] = None

    def _get_credential(self) -> ClientSecretCredential:
        if self._credential is None:
            missing = [
                name
                for name, value in (
                    ("AR_FABRIC_TENANT_ID", self._options.tenant_id),
                    ("AR_FABRIC_CLIENT_ID", self._options.client_id),
                    ("AR_FABRIC_CLIENT_SECRET", self._options.client_secret),
                )
                if not value
            ]
            if missing:
                raise KeyError(missing[0])
            self._credential = ClientSecretCredential(
                tenant_id=self._options.tenant_id,
                client_id=self._options.client_id,
                client_secret=self._options.client_secret,
            )
        return self._credential

    def _execute_dax_query(self, dax_query: str) -> list:
        token = self._get_credential().get_token(_POWERBI_SCOPE).token
        url = (
            f"https://api.powerbi.com/v1.0/myorg/groups/{self._options.workspace_id}"
            f"/datasets/{self._options.dataset_id}/executeQueries"
        )
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"queries": [{"query": dax_query}], "serializerSettings": {"includeNulls": True}},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["results"][0]["tables"][0]["rows"]

    def run_product_query(self, dax_query: str) -> FabricQueryResult:
        try:
            return FabricQueryResult.ok(self._execute_dax_query(dax_query))
        except KeyError as exc:
            logger.error("Missing required app setting: %s", exc)
            return FabricQueryResult.failed(f"Server misconfiguration: missing app setting {exc}.")
        except requests.HTTPError as exc:
            logger.error("Power BI executeQueries failed: %s", exc.response.text if exc.response is not None else exc)
            return FabricQueryResult.failed(
                "Failed to query product metrics. The MCP server's service principal may not "
                "yet have access to this Fabric workspace/model."
            )
        except ClientAuthenticationError as exc:
            logger.error("Service principal authentication failed: %s", exc)
            return FabricQueryResult.failed(
                "Failed to authenticate to Power BI. Check the AR_FABRIC_CLIENT_ID/TENANT_ID/CLIENT_SECRET app settings."
            )
