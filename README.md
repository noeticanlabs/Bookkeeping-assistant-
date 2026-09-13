# Bookkeeper Assistant

A small bookkeeping bridge for field-service businesses.

## KISS product boundary

`Work Order -> Cost -> Invoice -> Payment -> Bank -> Books`

If normal code can determine something exactly, do not ask AI.

The current usable MVP focuses on one workflow:

`Completed Job -> Prepare Invoice -> Issue Invoice -> Record Payment -> Record Bank Deposit -> Reconcile`

It also keeps optional hooks for field-service, accounting, document-reading, and event integrations so future ServiceTitan/Jobber/Housecall Pro/QuickBooks-style adapters can plug in without changing the core workflow.

## Run the app

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python web_app.py
```

Open `http://127.0.0.1:5000`.

Data is stored in `bookkeeper-data.json` by default. Set `BOOKKEEPER_DATA` to use another path and `BOOKKEEPER_SECRET` before any real deployment.

## Current capabilities

- completed work orders and quote totals
- job costs and vendor-bill cost treatment
- draft invoice preparation and invoice review
- invoice issuance
- partial/full customer payments
- processor fees and bank-deposit reconciliation
- accounts receivable and payable summaries
- near-term AR minus AP snapshot
- attention/exception queue
- JSON persistence
- optional connector hooks

## Integration hooks

`connectors.py` defines optional contracts for:

- field-service systems
- accounting systems
- document extraction
- event sinks / automation

Vendor-specific adapters are intentionally not implemented until a real integration is needed.

## Run tests

```bash
pytest -q
```

CI runs the complete test suite on every push to `kiss-fresh`.
