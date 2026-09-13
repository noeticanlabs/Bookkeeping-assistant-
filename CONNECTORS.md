# Live connector setup

Bookkeeper Assistant treats external systems as **capability providers**, not as the bookkeeping authority.

A company may connect multiple systems at the same time. For example:

- Yardi Maintenance -> `work_orders.read`
- ServiceTitan -> `work_orders.read`
- Jobber -> `work_orders.read`
- Housecall Pro -> `work_orders.read`
- QuickBooks Online -> `payments.read`, `deposits.read`
- Xero -> `payments.read`, `invoices.write`
- Stripe -> `payments.read`, `deposits.read`
- OpenAI Documents -> `documents.extract`

Pulled payments/deposits remain unmatched evidence until Bookkeeper Assistant's deterministic matching/review flow accepts them.

## Xero

Required for read access:

```bash
XERO_TENANT_ID=...
XERO_ACCESS_TOKEN=...
```

Optional invoice export:

```bash
XERO_REVENUE_ACCOUNT_CODE=200
XERO_CONTACT_IDS='{"Smith Residence":"xero-contact-guid"}'
```

Invoice export deliberately requires an explicit customer -> Xero `ContactID` map. The adapter does not guess contact identity from names.

The environment-based adapter expects a current OAuth bearer token. OAuth authorization, refresh-token rotation and encrypted credential storage belong in the connection-management layer, not in bookkeeping state.

## Stripe

```bash
STRIPE_SECRET_KEY=sk_live_...
```

The adapter imports:

- succeeded PaymentIntents -> `Payment` evidence
- paid Payouts -> `BankDeposit` evidence

Stripe amounts are converted from minor currency units to Bookkeeper Assistant decimal amounts. The current implementation assumes a two-decimal currency; multi-currency/zero-decimal handling must be added before those accounts are enabled.

## ServiceTitan

ServiceTitan API access must be provisioned through the customer's approved application/integration process. Once the customer has an approved jobs endpoint and credentials:

```bash
SERVICETITAN_JOBS_URL=https://...approved-jobs-endpoint...
SERVICETITAN_ACCESS_TOKEN=...
SERVICETITAN_APP_KEY=...
SERVICETITAN_FIELD_MAP='{"id":"jobId","customer":"customerName","description":"summary","status":"status"}'
SERVICETITAN_ITEMS_KEY=data
```

The ServiceTitan adapter contributes `WorkOrder` facts only. It does not infer costs, invoices or payments.

## Yardi Maintenance

Yardi Maintenance API details are supplied through the customer's authorized interface setup:

```bash
YARDI_BASE_URL=https://...
YARDI_WORK_ORDERS_PATH=...
YARDI_AUTH_HEADER=Authorization
YARDI_AUTH_VALUE=...
YARDI_FIELD_MAP='{"id":"WorkOrderId","customer":"Resident","description":"Problem","status":"Status"}'
YARDI_ITEMS_KEY=work_orders
YARDI_RESPONSE_FORMAT=json
```

Yardi is work-order-only unless another explicitly implemented capability is added.

## Jobber

```bash
JOBBER_ACCESS_TOKEN=...
JOBBER_GRAPHQL_VERSION=2025-04-16
```

## Housecall Pro

```bash
HOUSECALL_PRO_API_KEY=...
```

## QuickBooks Online

```bash
QBO_REALM_ID=...
QBO_ACCESS_TOKEN=...
QBO_SANDBOX=0
```

QuickBooks currently supplies Payment and Deposit evidence only.

## OpenAI document extraction

```bash
OPENAI_API_KEY=...
BOOKKEEPER_DOCUMENT_MODEL=gpt-5.6-luna
```

OpenAI extracts proposals only. It never directly mutates bookkeeping state.

## Security boundary

Do not put live OAuth tokens, refresh tokens, API keys, or secrets into CompanyProfile fields, Git, source code, audit events, or provenance records.

The current environment loader is appropriate for development and controlled deployments. A company-facing Connect/Disconnect UI requires a dedicated encrypted credential store and OAuth callback/refresh management.
