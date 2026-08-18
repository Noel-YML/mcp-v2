"""The 3 real ARIEL product tools an agent can call. Each one builds a DAX
query from products.catalog + products.dax_query_builder, then runs it
through the injected IFabricQueryService - so these can be tested against a
fake fabric service instead of live Fabric.

The module-level functions below are the hosting-agnostic implementations.
`register` wires them up as MCP SDK tools (used by the Streamable HTTP /
container hosting in server.py). The Azure Functions hosting in
function_app.py calls these same functions directly instead, so the DAX/error
handling logic isn't duplicated across the two hosting models.
"""

import json

from fabric_client.result import FabricQueryResult
from fabric_client.service import IFabricQueryService
from products import dax_query_builder
from products.catalog import PRODUCT_MEASURES, PRODUCT_NAMES_HELP, PRODUCT_PRESENCE_MEASURE


def get_product_automation_metrics(
    fabric: IFabricQueryService, product_name: str, hotel_name: str | None = None
) -> str:
    measures = PRODUCT_MEASURES.get(product_name)
    if measures is None:
        return f"No automation metrics are tracked for product '{product_name}'. Available products: {PRODUCT_NAMES_HELP}."

    dax_query = dax_query_builder.build_snapshot_query(measures, hotel_name)
    result = fabric.run_product_query(dax_query)
    return _format_snapshot_result(result, product_name, hotel_name)


def list_available_products(fabric: IFabricQueryService, hotel_name: str | None = None) -> str:
    dax_query = dax_query_builder.build_snapshot_query(PRODUCT_PRESENCE_MEASURE, hotel_name)
    result = fabric.run_product_query(dax_query)
    if result.error:
        return result.error
    if not result.rows:
        scope = f"hotel '{hotel_name}'" if hotel_name else "the organization"
        return f"No data found for {scope} at all. Check that the hotel name is spelled exactly as it appears in the system."

    row = result.rows[0]
    with_data = [product for product in PRODUCT_PRESENCE_MEASURE if (row.get(f"[{product}]") or 0) > 0]
    without_data = [product for product in PRODUCT_PRESENCE_MEASURE if product not in with_data]
    return json.dumps({"products_with_data": with_data, "products_without_data": without_data})


def get_product_automation_trend(
    fabric: IFabricQueryService,
    product_name: str,
    hotel_name: str | None = None,
    months: int | None = None,
) -> str:
    measures = PRODUCT_MEASURES.get(product_name)
    if measures is None:
        return f"No automation metrics are tracked for product '{product_name}'. Available products: {PRODUCT_NAMES_HELP}."

    dax_query = dax_query_builder.build_trend_query(measures, hotel_name, months or 12)
    result = fabric.run_product_query(dax_query)
    if result.error:
        return result.error
    if not result.rows:
        scope = f"hotel '{hotel_name}'" if hotel_name else "the organization"
        return f"No {product_name} history found for {scope}. Check that the hotel name is spelled exactly as it appears in the system."

    cleaned = []
    for row in result.rows:
        cleaned.append(
            {
                ("month" if key == "_Dates[MonthInCalendar]" else key.strip("[]")): value
                for key, value in row.items()
                if key != "_Dates[MonthnYear]"
            }
        )
    return json.dumps(cleaned)


def _format_snapshot_result(result: FabricQueryResult, product_name: str, hotel_name: str | None) -> str:
    if result.error:
        return result.error
    if not result.rows:
        scope = f"hotel '{hotel_name}'" if hotel_name else "the organization"
        return f"No {product_name} data found for {scope}. Check that the hotel name is spelled exactly as it appears in the system."
    return json.dumps(result.rows[0])


def register(mcp, fabric: IFabricQueryService) -> None:
    @mcp.tool(name="get_product_automation_metrics")
    def _get_product_automation_metrics(product_name: str, hotel_name: str | None = None) -> str:
        """Gets ARIEL automation and labour-savings metrics for a specific product
        (AR Invoices Automation, VCC Automation, Reservation Module, Audit,
        Booking.com Reconciliation, Fastcom Reconciliation, or DMR), for a hotel
        or organization-wide if no hotel is given.

        Args:
            product_name: Exact product name. One of: AR Invoices Automation, Audit,
                Booking.com Reconciliation, DMR, Fastcom Reconciliation,
                Reservation Module, VCC Automation.
            hotel_name: Exact hotel name to filter by, e.g. 'IBIS Brisbane Airport'.
                Omit for organization-wide totals across all hotels.
        """
        return get_product_automation_metrics(fabric, product_name, hotel_name)

    @mcp.tool(name="list_available_products")
    def _list_available_products(hotel_name: str | None = None) -> str:
        """Lists which ARIEL products actually have data for a hotel (or
        organization-wide if no hotel is given). Use this before asking about a
        specific product if you don't already know which ones apply.

        Args:
            hotel_name: Exact hotel name to check, e.g. 'IBIS Brisbane Airport'.
                Omit to check organization-wide.
        """
        return list_available_products(fabric, hotel_name)

    @mcp.tool(name="get_product_automation_trend")
    def _get_product_automation_trend(
        product_name: str, hotel_name: str | None = None, months: int | None = None
    ) -> str:
        """Gets a month-by-month trend (one row per month, most recent last) of
        automation metrics for a specific product, for a hotel or
        organization-wide if no hotel is given. Use this for questions about
        trends, history, or month-over-month change - get_product_automation_metrics
        only returns a single current snapshot, not a series.

        Args:
            product_name: Exact product name. One of: AR Invoices Automation, Audit,
                Booking.com Reconciliation, DMR, Fastcom Reconciliation,
                Reservation Module, VCC Automation.
            hotel_name: Exact hotel name to filter by. Omit for organization-wide totals.
            months: How many of the most recent months to return. Defaults to 12.
        """
        return get_product_automation_trend(fabric, product_name, hotel_name, months)
