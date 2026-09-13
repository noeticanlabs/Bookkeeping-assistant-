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

## Real receipt/vendor-invoice extraction

The runnable app automatically enables the OpenAI document connector when `OPENAI_API_KEY` is present.

```bash
export OPENAI_API_KEY="your-key"
# optional; defaults to gpt-5.6-luna
export BOOKKEEPER_DOCUMENT_MODEL="gpt-5.6-luna"
python web_app.py
```

Supported upload types are PDF, PNG, JPG/JPEG, and WEBP, up to 20 MB by default.

The document model only proposes structured fields such as vendor, amount, document/reference ID, explicit work-order ID, and whether the document appears to be a vendor bill or paid cost. Extraction never posts accounting state. The user reviews and may edit the proposal before approval.

`OPENAI_API_KEY` is read from the environment only; it is not stored in the repository or bookkeeping JSON file.

## Current capabilities

- completed work orders and quote totals
- CSV work-order import
- live field-service work-order sync hook
- real receipt/vendor-invoice interpretation through an optional OpenAI connector
- human review before document-derived bookkeeping records are created
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

## Work-order import contract

All external sources normalize into the same internal `WorkOrder` model.

CSV files require:

```text
id,customer,description
```

and may include:

```text
status,quoted_total
```

Example:

```csv
id,customer,description,status,quoted_total
WO-1842,Smith Residence,Water heater replacement,complete,2450.00
```

A live field-service adapter implements `pull_work_orders()` and returns normalized `WorkOrder` objects. CSV import and live connector sync then use the exact same import service, duplicate handling, persistence, invoice workflow, and downstream bookkeeping rules.

## Integration hooks

`connectors.py` defines optional contracts for:

- field-service systems: pull work orders and issue invoices
- accounting systems: push invoices and pull payments/deposits
- document extraction: convert receipts/bills into structured proposals
- event sinks / automation: observe workflow events without owning bookkeeping state

Vendor-specific adapters remain thin transport/translation layers. They should not contain bookkeeping policy that belongs in the core.

## Run tests

```bash
pytest -q
```

CI runs the complete test suite on every push to `kiss-fresh`.
