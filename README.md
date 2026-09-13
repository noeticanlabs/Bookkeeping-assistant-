# Bookkeeper Assistant

A small bookkeeping bridge for field-service businesses.

## What it does

Connects:

`Work Order -> Costs -> Invoice -> Payment -> Books`

The first core can:

- track work orders
- attach job costs
- review completed jobs for missing or mismatched invoices
- track payments and balances due
- calculate simple job cost and profit
- flag unmatched costs and payments
- connect to field-service/accounting systems through small adapters

## KISS rule

If normal code can determine something exactly, do not ask AI.

AI will later be used only where interpretation helps: reading documents, suggesting uncertain matches/classifications, explaining exceptions, and detecting unusual records.

The database and connected business systems remain the source of record.

## Run tests

```bash
pytest -q
```
