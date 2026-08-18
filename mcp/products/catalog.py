"""Static data: for each ARIEL product, the DAX measure names/expressions
that make up its automation metrics. No logic, no I/O - each product's
measures are self-scoped in their own DAX expression (no Product[Product_Name]
filter needed), so this is just a lookup table.
"""

PRODUCT_MEASURES = {
    "AR Invoices Automation": {
        "Fetched Invoices": "[AR Invoices - Fetched Invoices]",
        "ARIEL Sent Invoices": "[AR Invoices - ARIEL Sent Invoices]",
        "Manual Sent Invoices": "[AR Invoices - Manual Sent Invoices]",
        "Pending Invoices": "[AR Invoices - Pending Invoices]",
        "Automation Rate": "[AR Invoices - Automation Rate (%)]",
        "Labour Savings (hours)": "[AR Invoices - Labour Savings (hours)]",
        "Labour Savings ($)": "[AR Invoices - Labour Savings ($)]",
        "Labour Savings (FTE)": "[AR Invoices - Labour Savings (FTE)]",
    },
    "Audit": {
        "Transactions Processed": "[Audit - Transactions Processed]",
        "Pages Processed": "[Audit - Pages Processed]",
        "Audit Time (hours)": "[Audit - Transactions-based Audit Time (hours)]",
        "Audit Costs ($)": "[Audit - Transactions-based Audit Costs ($)]",
        "Printing Cost Savings ($)": "[Audit - Printing Cost Savings ($)]",
        "Total Savings ($)": "[Audit - Total Savings ($)]",
        "Labour Savings (FTE)": "[Audit - Labour Savings (FTE)]",
    },
    "Booking.com Reconciliation": {
        "Transaction Count": "[Booking.com - Transaction Count]",
        "Total Overcharge": "[Booking.com - Total Overcharge]",
        "Labour Savings (hours)": "[Booking.com - Labour Savings (hours)]",
        "Labour Savings ($)": "[Booking.com - Labour Savings ($)]",
        "Labour Savings (FTE)": "[Booking.com - Labour Savings (FTE)]",
    },
    "DMR": {
        "Reports": "[DMR - Reports]",
        "Labour Savings (hours)": "[DMR - Labour Savings (hours)]",
        "Labour Savings ($)": "[DMR - Labour Savings ($)]",
        "Labour Savings (FTE)": "[DMR - Labour Savings (FTE)]",
    },
    "Fastcom Reconciliation": {
        "Transaction Count": "[Fastcom - Transaction Count]",
        "Total Overcharge": "[Fastcom - Total Overcharge]",
        "Labour Savings (hours)": "[Fastcom - Labour Savings (hours)]",
        "Labour Savings ($)": "[Fastcom - Labour Savings ($)]",
        "Labour Savings (FTE)": "[Fastcom - Labour Savings (FTE)]",
    },
    "Reservation Module": {
        "Fetched Reservations": "[ResTBR - Total Fetched Reservations]",
        "ARIEL Completed Reservations": "[ResTBR - ARIEL Completed Reservations]",
        "Manually Completed Reservations": "[ResTBR - Manually Completed Reservations]",
        "Pending Reservations": "[ResTBR - Pending Reservations]",
        "Automation Rate": "[ResTBR - Automation Rate (%)]",
        "Labour Savings (hours)": "[ResTBR - Labour Savings (hours)]",
        "Labour Savings ($)": "[ResTBR - Labour Savings ($)]",
        "Labour Savings (FTE)": "[ResTBR - Labour Savings (FTE)]",
    },
    "VCC Automation": {
        "Total VCC Payments Identified": "[VCC - Total VCC Payments Identified]",
        "ARIEL Handled Payments": "[VCC - ARIEL Handled Payments]",
        "Manually Handled Payments": "[VCC - Manually Handled Payments]",
        "Pending VCC Bookings": "[VCC - Pending VCC Bookings]",
        "Automation Rate": "[VCC - Automation Rate (%)]",
        "Automation Rate ex. Pending": "[VCC - Automation Rate (%) ex. Pending]",
        "Labour Savings (hours)": "[VCC - Labour Savings (hours)]",
        "Labour Savings ($)": "[VCC - Labour Savings ($)]",
        "Labour Savings (FTE)": "[VCC - Labour Savings (FTE)]",
    },
}

PRODUCT_NAMES_HELP = ", ".join(PRODUCT_MEASURES.keys())

# A single "how much activity happened" measure per product, used to answer
# "which products have data" without querying every product's full metric set.
PRODUCT_PRESENCE_MEASURE = {
    "AR Invoices Automation": "[AR Invoices - Fetched Invoices]",
    "Audit": "[Audit - Transactions Processed]",
    "Booking.com Reconciliation": "[Booking.com - Transaction Count]",
    "DMR": "[DMR - Reports]",
    "Fastcom Reconciliation": "[Fastcom - Transaction Count]",
    "Reservation Module": "[ResTBR - Total Fetched Reservations]",
    "VCC Automation": "[VCC - Total VCC Payments Identified]",
}
