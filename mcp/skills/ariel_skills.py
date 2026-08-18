"""Agent Skills exposed as MCP resources (skill-md distribution: a SKILL.md
document per skill, fetched on demand) - ported from the C# port's
Skills/AskArielSkills.cs. Unlike tools/, these carry no logic and make no
Fabric calls - they are pure guidance text the agent loads only when a task
matches.

The two SKILL.md constants are the hosting-agnostic content; `register` wires
them up as MCP SDK resources (server.py / container hosting). The Azure
Functions hosting in function_app.py reads the same constants directly via
`@app.mcp_resource_trigger`.
"""

INTERPRETING_ARIEL_METRICS = """\
---
name: interpreting-ariel-metrics
description: How to read and narrate ARIEL automation metrics without overstating what they mean
---

# Interpreting ARIEL Automation Metrics

This skill governs how to talk about the numbers returned by
`get_product_automation_metrics`, `list_available_products`, and
`get_product_automation_trend`. It exists because these are real
governed metrics with specific, narrow meanings - not general-purpose
business KPIs you can freely reinterpret.

## What each field means

- **Fetched Invoices / Identified count** - the eligible volume of work
  ARIEL could act on in the window. This is the denominator for automation
  rate, not a workload or revenue figure.
- **ARIEL Sent / Automated count** - eligible work ARIEL completed
  without manual intervention.
- **Manual count** - eligible work that required a human to complete.
- **Pending count** - eligible work not yet completed as of the data
  freshness cutoff. A high pending count on its own does not mean
  something is broken - check whether it's a snapshot artifact of the
  cutoff date before calling it an issue.
- **Automation Rate** - `Automated / Eligible` for that product only.
  It is a ratio of governed sums, not something you can derive by
  averaging or estimating.
- **Labour Savings (hours / $ / FTE)** - hours ARIEL avoided, valued
  using the approved labour-rate policy, and the FTE-equivalent of
  those hours.

## Hard rules - do not violate these

1. **FTE is not a staffing claim.** Never say or imply that ARIEL
   "reduced," "eliminated," or "replaced" a fraction of a role. State it
   as hours saved, and only mention the FTE figure as a scale reference
   if asked directly.
2. **Labour value is not profit, revenue, or cash savings.** It is hours
   saved multiplied by a labour-rate policy. Do not describe it as money
   the hotel "made" or "kept."
3. **A product with no automation-rate measure is "not applicable," not
   zero and not unhealthy.** Some ARIEL products (e.g. Audit, DMR) don't
   have a governed automation-rate denominator. If a rate is missing,
   say the metric doesn't apply to that product - don't imply the
   product failed to report, and don't substitute 0%.
4. **Never sum or average automation rates across products.** Each
   product's eligible-transaction population is different and not
   proven commensurable. A blended "portfolio automation rate" is not a
   real number unless it comes back from the query as one.
5. **A single low automation rate is a signal to investigate, not a
   diagnosed root cause.** Use "worth investigating" language, not
   causal claims ("this failed because...").
6. **Check `list_available_products` before asserting a product has no
   data for a hotel.** A tool returning no rows for one product call
   usually means wrong scope or wrong product name, not that ARIEL
   definitely has zero presence there.

## Reading a trend (`get_product_automation_trend`)

- Rows are ordered oldest to most recent (most recent last).
- The most recent month may be a **partial period** - before describing
  a drop or spike in the latest month, consider whether the month is
  simply not finished yet.
- Don't extrapolate a multi-month trend from two data points; if only
  one or two months of data exist, say so rather than calling it a trend.

## Good vs. bad narration

- Good: "AR Invoices Automation ran at 87% automation this period,
  saving about 785 labour hours."
- Bad: "AR Invoices Automation saved 0.36 FTE, meaning ARIEL did more
  than a third of a person's job" - states a real number but draws a
  staffing conclusion the data doesn't support.
- Good: "Audit doesn't have an automation-rate measure - that's expected
  for this product, not a sign of a problem."
- Bad: "Audit's automation rate must be low since it wasn't returned."
"""

HOTEL_BUSINESS_ONTOLOGY = """\
---
name: hotel-business-ontology
description: Disambiguates hospitality-domain terms (operator vs. manager, booking vs. subscription, AR, audit) for correctly interpreting user questions
---

# Hotel Business Ontology

A business-first conceptual model of the hospitality domain this system
describes, centered on the Hotel entity. Use this to resolve ambiguous
terms before answering - most confusion in this domain comes from words
that mean different things depending on context (e.g. "booking",
"subscription", "manager").

## Scope note for this MCP server - read this first

This ontology describes the *full* hotel business domain. **Only the
Hotel and Product/Automation-metric portions are actually queryable
through this server's tools today** (`get_product_automation_metrics`,
`list_available_products`, `get_product_automation_trend`, scoped by
hotel name and ARIEL product name only).

Rooms, Operations, Reservations, Accounts Receivable, Audit, REL/Product
Subscriptions, and Opportunities/CRM are documented below for
*disambiguation context* - so you correctly understand what a user means
when they say "subscription" or "who manages this hotel" - but there is
**no tool on this server that queries them directly.** If a question
actually requires that data (e.g. "what's this hotel's AR aging?"), say
that this server doesn't have that capability rather than guessing or
fabricating an answer from the automation-metric tools.

## Core entities

### Hotel (root entity)
The single operating hospitality property everything else relates to.
Also called: property, asset, site, location. When a user says "the
property" or "this hotel," resolve to a specific hotel before answering
- almost every question here is scoped by hotel. "Our portfolio" / "all
properties" means a rollup across hotels.

### Operating Company vs. Management Company - the most commonly confused pair
- **Operating Company**: who runs the hotel day-to-day (staffing, guest
  experience, execution).
- **Management Company**: who provides strategic oversight, brand
  standards, and financial reporting oversight - usually the primary
  commercial relationship for subscriptions/reporting products.
- These can be the *same* legal entity or *different* ones for a given
  hotel. Never assume without checking; if asked "who manages this
  hotel," clarify which sense (operational vs. strategic) if it matters
  to the question.

### Rooms
Physical, sellable inventory. Distinguish "rooms" as inventory (a room
type) from a specific stay booking, which is a Reservation.

### Operations
Day-to-day performance: occupancy, ADR, RevPAR, staffing, service scores.
"How is the hotel doing" is often really asking for one of these
specific KPIs, a narrative summary, or a specific issue - clarify which.

### Reservations ("bookings")
A guest's confirmed/pending/cancelled stay commitment. "Booking" and
"reservation" are synonyms here. **Do not confuse with a REL/Product
Subscription** - a subscription is a business's subscription to an
ARIEL product, not a guest booking a room, even though both involve the
word "booking a service."

### Accounts Receivable (AR)
Money owed for delivered services/subscriptions, not yet collected.
Tracked by aging bucket (current, 30/60/90+ days). **Cover date vs.
invoice date mismatches are a known, expected source of apparent
discrepancies** - a charge's cover date (the period it relates to) can
legitimately differ from its invoice date. Don't assume a data or logic
error before checking for this specific timing pattern.

### Audit
Compliance, controls, and verification - spans financial audit (is this
AR number accurate) and security/compliance audit (SOC 2 controls,
access reviews). Clarify which sense a user means: a formal compliance
program, or a casual "let's verify this number" request.

### REL / Product Subscriptions
A hotel's (or Management/Operating Company's) subscription to an ARIEL
product - what they have access to, for how long, on what terms, billed
to whom. "REL" is internal shorthand for this subscription/product
relationship record. This is distinct from a guest Reservation.

### Opportunities / CRM
Sales pipeline: new signings, upsells, renewals. Every subscription
traces back to the opportunity that created it.

## Relationship summary

Hotel has Rooms, is run by an Operating Company, is overseen by a
Management Company, generates Reservations, produces AR, is reviewed by
Audit, holds Subscriptions, and is targeted by Opportunities.
Reservations drive Operations and generate AR. Subscriptions generate
AR. AR is validated by Audit. Opportunities convert into Subscriptions
when won.

## Practical rules for answering questions

- Anchor every query to a specific hotel unless the question is
  explicitly portfolio-wide.
- Disambiguate Operating Company vs. Management Company explicitly
  rather than assuming.
- Treat "bookings" and "reservations" as synonyms; treat "subscription"
  (REL/product) and "reservation" (guest stay) as entirely separate
  concepts.
- For AR discrepancy questions, check for cover-date vs. invoice-date
  timing before concluding there's an error.
- Remember the scope note above: only Hotel + Product/Automation-metric
  data is actually queryable here. Everything else in this ontology is
  context for understanding the question, not something this server
  can look up.
"""


def register(mcp) -> None:
    @mcp.resource(
        "skill://ariel/interpreting-ariel-metrics/SKILL.md",
        name="interpreting-ariel-metrics",
        mime_type="text/markdown",
        description=(
            "How to interpret and narrate the automation-rate, labour-savings, and "
            "FTE numbers returned by get_product_automation_metrics and "
            "get_product_automation_trend - including what these numbers must never "
            "be implied to mean. Load this before answering any question that cites "
            "a specific metric value."
        ),
    )
    def interpreting_ariel_metrics() -> str:
        return INTERPRETING_ARIEL_METRICS

    @mcp.resource(
        "skill://ariel/hotel-business-ontology/SKILL.md",
        name="hotel-business-ontology",
        mime_type="text/markdown",
        description=(
            "The business ontology for the hospitality domain this data describes "
            "(Hotel, Operating Company vs. Management Company, Reservations, AR, "
            "Audit, Subscriptions, Opportunities) - including which of these are "
            "actually queryable through this MCP server today versus background "
            "context only. Load this when a question uses ambiguous domain terms "
            "(e.g. 'who manages this hotel', 'subscription', 'booking')."
        ),
    )
    def hotel_business_ontology() -> str:
        return HOTEL_BUSINESS_ONTOLOGY
