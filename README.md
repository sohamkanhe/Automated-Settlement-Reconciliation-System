# Automated Settlement Reconciliation System (AutoRecon)

> A production-grade multi-pass reconciliation engine and interactive dashboard built with **React**, **FastAPI**, **MySQL 8.0**, and **Docker**.  
> Designed to solve the month-end books imbalance for high-volume payments platforms, identify root causes, and account for every cent down to **$0.00 unexplained variance**.

---

## 1. Executive Summary & Problem Statement

At month end, a payments platform's internal transaction ledger does not match what the acquiring bank statements say actually arrived:
- **Internal Platform Volume Recorded**: **$289,494.04** (1,153 processed transactions)
- **Bank Settlement Volume Deposited**: **$274,171.63** (1,144 settlement payouts)
- **Raw Month-End Imbalance**: **$15,322.41**

Without automated reconciliation, the finance and accounting teams cannot close the books, suspect missing funds or rogue leakage, and face audit compliance risk.

**AutoRecon** runs a deterministic multi-pass reconciliation algorithm that analyzes transaction metadata, payment gateway clearing references, settlement date lags, fee calculation tiers, and batch idempotency to uncover the exact discrepancies.

---

## 2. Root Cause Analysis: The 4 Planted Gaps

| # | Gap Type | Count | Financial Impact | Root Cause & Real-World Mechanism | Recommended Accounting Solution |
|---|---|---|---|---|---|
| **1** | **Settled Following Month (Timing / Cut-off)** | **12 txns** | **$14,315.50** | Platform processed transactions in the final 48 hours of October (Oct 30–31). Due to banking settlement rails (T+2/T+3 business days), funds arrived in early November (Nov 2–3). | Record Balance Sheet Adjusting Journal Entry: Debit *Cash in Transit / Clearing Asset* $14,315.50, Credit *Undeposited Funds*. (Funds are safe and verified). |
| **2** | **Cumulative Rounding Drift (Sub-cent Sum)** | **350 txns** | **$5.31** | Platform calculates processing fees per-transaction using standard round-half-up (2 decimals: $1.16). Acquiring banks compute tiered interchange at 4 decimals and batch-aggregate before rounding ($1.14). Each transaction variance is negligible ($0.01), but across hundreds of batches, it creates a systemic cumulative discrepancy. | Post monthly adjusting entry to General Ledger: Credit *Bank Processing Fee Expense* $5.31 against *Bank Clearing Account*. |
| **3** | **Duplicate Entries (Bank & Platform)** | **2 items** | **$290.40** | **Sub-case 3A (Bank Duplicate)**: Batch transmission glitch caused bank to deposit $265.62 twice for `TXN_202610_00015`.<br>**Sub-case 3B (Platform Duplicate)**: Checkout network timeout caused a customer retry creating duplicate internal row `TXN_202610_DUP_8802` ($24.78), while payment gateway deduplicated and billed customer once. | **3A**: Initiate processor clawback / debit memo to return the $265.62 over-credit.<br>**3B**: Void redundant internal platform row $24.78 to correct overstated revenue. |
| **4** | **Orphan Refunds (Bank Debits Without Sales)** | **3 debits** | **-$1,247.75** | Bank statement contains debits for customer chargebacks/refunds ($320.00, $785.50, $142.25), but the platform database has no matching sales or refund record (e.g. forced merchant portal refund or legacy dispute). | Audit processor dispute portal logs, match dispute references, and record manual entry to *Dispute & Chargeback Expense*. |

---

## 3. Financial Reconciliation Waterfall (The Mathematical Proof)

The table below demonstrates how the **$15,322.41** raw discrepancy is mathematically resolved to **$0.00**:

```
========================================================================================
STEP   RECONCILIATION LINE ITEM                        AMOUNT        SUBTOTAL / STATUS
========================================================================================
 1.    Platform Ledger Gross Volume                 +$289,494.04    Platform Starting Book
 2.    Less: Settled Following Month (Timing Lag)   -$ 14,315.50    Funds in Nov Statement
 3.    Less: Platform Duplicate Retries (Phantom)   -$     24.78    Void Internal Record
----------------------------------------------------------------------------------------
 4.    ADJUSTED PLATFORM SALES                      +$275,153.76    Legitimate Oct Sales
----------------------------------------------------------------------------------------
 5.    Bank Statement Gross Received                +$274,171.63    Bank Starting Deposits
 6.    Less: Bank Duplicate Over-credits            -$    265.62    Processor Clawback
 7.    Add Back: Orphan Bank Debits (No Sale Match) +$  1,247.75    Dispute Loss Outflow
----------------------------------------------------------------------------------------
 8.    ADJUSTED BANK SALES DEPOSITED                +$275,153.76    Legitimate Oct Settled
----------------------------------------------------------------------------------------
 9.    NET UNEXPLAINED DISCREPANCY                         $0.00    100% RECONCILED
========================================================================================
```

---

## 4. Architecture & System Overview

```
                        ┌────────────────────────────────────────────────────────┐
                        │                   React Web UI (3000)                  │
                        │  - Executive KPI Cards     - Gap Spotlight Panels      │
                        │  - Waterfall Bridge Chart  - Interactive Data Grid     │
                        │  - Side-by-side Inspector  - Test Data Generator Modal │
                        └───────────────────────────┬────────────────────────────┘
                                                    │ Reverse Proxy (/api)
                                                    ▼
                        ┌────────────────────────────────────────────────────────┐
                        │             Nginx Web Server Container                 │
                        └───────────────────────────┬────────────────────────────┘
                                                    │
                                                    ▼
                        ┌────────────────────────────────────────────────────────┐
                        │               FastAPI Backend (8000)                   │
                        │  - Multi-pass Deterministic Matching Engine            │
                        │  - Timing Lag Cross-Month Window Resolver              │
                        │  - Sub-cent Cumulative Rounding Calculator             │
                        │  - Idempotency & Duplicate Conflict Resolver           │
                        │  - Audit Trail & CSV Export Service                    │
                        └───────────────────────────┬────────────────────────────┘
                                                    │ SQLAlchemy / PyMySQL
                                                    ▼
                        ┌────────────────────────────────────────────────────────┐
                        │                   MySQL 8.0 (3306)                     │
                        │  - internal_transactions   - bank_settlements          │
                        │  - reconciliation_runs     - reconciliation_items      │
                        │  - audit_actions           - persistent volume         │
                        └────────────────────────────────────────────────────────┘
```

---

## 5. Quickstart & Deployment

### Prerequisites
- [Docker](https://www.docker.com/) & Docker Compose installed.

### Start the System (Single Command)
```bash
docker compose up -d
```

Containers initialized:
1. `autorecon-mysql`: MySQL 8.0 database (Port 3306) with healthchecks.
2. `autorecon-backend`: FastAPI engine (Port 8000), auto-migrates tables and auto-seeds initial dataset.
3. `autorecon-frontend`: React dashboard served via Nginx (Port 3000).

### Access Application
- **Interactive Web Dashboard**: [http://localhost:3000](http://localhost:3000)
- **FastAPI OpenAPI Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

## 6. Standalone Data Generator (`data_generator.py`)

The root `data_generator.py` script can be executed independently to generate test data, or controlled via the UI/API.

```bash
# Run locally inside python environment or docker:
docker run --rm -v "${PWD}:/app" -w /app python:3.11-slim python data_generator.py
```

### Planted Gap Parameters in Generator:
- `num_transactions`: Configurable transaction volume (default `1,200`).
- `seed`: Random seed for deterministic reproducibility (default `42`).
- `timing_gap_count`: Number of late-month transactions pushed to November (`12`).
- `rounding_txns_count`: Number of transactions with sub-cent interchange variances (up to `350`).
- `duplicates`: Injects bank duplicate settlement and platform checkout retry.
- `orphan_refunds`: Injects 3 bank debit refunds missing original platform sales.

---

## 7. API Endpoints Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/reconcile/latest` | Returns latest reconciliation run metrics, waterfall steps, and $0.00 unexplained proof. |
| `GET` | `/api/reconcile/gaps-summary` | In-depth breakdown of the 4 planted gaps with root cause analysis and accounting recommendations. |
| `GET` | `/api/reconcile/items` | Filterable, paginated reconciliation records (`status`, `category`, `search`, `gaps_only`). |
| `POST` | `/api/reconcile` | Triggers a fresh reconciliation run for a given month/year. |
| `POST` | `/api/data/generate` | Generates a new synthetic payments dataset, seeds MySQL, and auto-runs reconciliation. |
| `POST` | `/api/audit/action` | Records an auditor resolution or adjustment note on a discrepancy line item. |
| `GET` | `/api/audit/logs` | Fetches the full compliance audit trail. |
| `GET` | `/api/export` | Downloads the complete reconciliation report as a CSV file. |
| `GET` | `/api/transactions` | Lists raw platform internal transactions. |
| `GET` | `/api/settlements` | Lists raw bank settlements. |

---

## 8. Audit Trail & Compliance Workflow

Every discrepancy item in the dashboard can be investigated via the **Discrepancy Inspector Modal**:
1. Inspects raw platform metadata vs bank settlement clearing references.
2. Explains the financial mechanics (why it occurred).
3. Gives concrete CFO/accounting steps (e.g. accrual, clawback, fee journal entry).
4. Allows compliance auditors to log actions (`MARK_RESOLVED`, `ACCRUE_IN_TRANSIT`, `INITIATE_CLAWBACK`, `NOTE_ADDED`), timestamped and recorded in the immutable audit table.

---

## 9. Teardown
```bash
docker compose down -v
```
To stop all containers and remove the database volume.
