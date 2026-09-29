# Security Policy

Bookkeeper Assistant handles bookkeeping evidence, credentials, approvals, and external-system integrations. Please treat security reports carefully.

## Supported version

The `main` branch is the current supported development line. Historical branches are retained for research and certification history and should not be assumed to receive security fixes.

## Reporting a vulnerability

Please do **not** disclose exploitable vulnerabilities in a public GitHub issue.

Use GitHub's private vulnerability reporting / Security Advisory feature for this repository when available. If private reporting is unavailable, contact the repository owner privately through an established Noetican Labs contact channel before publishing technical details.

Include, where possible:

- affected commit/version;
- reproduction steps;
- expected and observed behavior;
- security impact;
- whether credentials, authority, financial state, external mutations, or customer data are affected;
- a minimal proof of concept that does not contain real secrets or private customer data.

## High-priority classes

Reports are especially important when they involve:

- authentication or authorization bypass;
- AI acquisition of execution credentials or connector capabilities;
- bypass of the Action Firewall;
- forged or replayed approvals;
- mutation of an approved proposal without invalidating approval;
- duplicate external execution after an uncertain outcome;
- audit/provenance tampering;
- credential or token disclosure;
- cross-company data exposure;
- unsafe file handling or injection paths.

## Threat-model boundary

The governed-AI certification tests establish application-level capability, credential, proposal, approval, and dispatch boundaries. They do not claim resistance to arbitrary operating-system, host, dependency, administrator, or physical compromise.

Security claims should be scoped to the exact property and test boundary that supports them.

## Secrets and test data

Never submit real API keys, OAuth credentials, banking data, customer financial records, production databases, or private evidence documents in issues, pull requests, fixtures, screenshots, or logs. Use synthetic test data.