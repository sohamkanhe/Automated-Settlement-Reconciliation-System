import io
import csv
import logging
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, func

from .config import settings
from .database import engine, get_db, wait_for_db, Base
from .models import (
    InternalTransaction, BankSettlement, ReconciliationRun,
    ReconciliationItem, AuditAction
)
from .schemas import (
    ReconciliationRunSchema, ReconciliationItemSchema,
    InternalTransactionSchema, BankSettlementSchema,
    AuditActionCreate, AuditActionSchema,
    GenerateDataRequest, ReconcileRequest
)
from .engine import ReconciliationEngine
from data_generator import generate_reconciliation_datasets

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("reconciliation.api")

app = FastAPI(
    title="Automated Settlement Reconciliation System API",
    description="Engine for detecting month-end settlement gaps, timing differences, duplicates, and rounding variances",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    """Wait for MySQL database, initialize schema and seed initial dataset if empty."""
    logger.info("Initializing database...")
    wait_for_db()
    Base.metadata.create_all(bind=engine)
    logger.info("Tables created or verified.")

    # Check if data already exists
    with next(get_db()) as db:
        tx_count = db.query(InternalTransaction).count()
        if tx_count == 0:
            logger.info("No existing transactions detected. Generating initial synthetic data...")
            seed_synthetic_data(db, num_transactions=1200, month=10, year=2026, seed=42)
            logger.info("Executing initial reconciliation...")
            engine_recon = ReconciliationEngine(db)
            engine_recon.run_reconciliation(year=2026, month=10)
            logger.info("Initial reconciliation completed successfully.")

def seed_synthetic_data(db: Session, num_transactions: int = 1200, month: int = 10, year: int = 2026, seed: int = 42):
    """Seed synthetic transactions and settlements into MySQL."""
    datasets = generate_reconciliation_datasets(
        num_transactions=num_transactions,
        month=month,
        year=year,
        seed=seed
    )

    itxn_objs = [
        InternalTransaction(**item) for item in datasets["internal_transactions"]
    ]
    settle_objs = [
        BankSettlement(**item) for item in datasets["bank_settlements"]
    ]

    # Reconciliation commits the replacement dataset and its run together.
    db.query(AuditAction).delete()
    db.query(ReconciliationItem).delete()
    db.query(ReconciliationRun).delete()
    db.query(BankSettlement).delete()
    db.query(InternalTransaction).delete()

    db.bulk_save_objects(itxn_objs)
    db.bulk_save_objects(settle_objs)
    logger.info(f"Seeded {len(itxn_objs)} internal txns and {len(settle_objs)} bank settlements.")

@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Automated Settlement Reconciliation System"}

@app.post("/api/data/generate")
def generate_data(req: GenerateDataRequest, db: Session = Depends(get_db)):
    """Generate fresh dataset with planted gaps and seed database."""
    seed_synthetic_data(db, num_transactions=req.num_transactions, month=req.month, year=req.year, seed=req.seed)
    engine_recon = ReconciliationEngine(db)
    result = engine_recon.run_reconciliation(year=req.year, month=req.month)
    return {
        "status": "success",
        "message": f"Successfully generated and reconciled {req.num_transactions} transactions.",
        "reconciliation": result
    }

@app.post("/api/reconcile")
def run_reconciliation(req: ReconcileRequest, db: Session = Depends(get_db)):
    """Trigger month-end reconciliation engine."""
    engine_recon = ReconciliationEngine(db)
    result = engine_recon.run_reconciliation(year=req.year, month=req.month)
    return result

@app.get("/api/reconcile/latest")
def get_latest_reconciliation(db: Session = Depends(get_db)):
    """Fetch the latest reconciliation run and summary metrics."""
    latest_run = db.query(ReconciliationRun).order_by(desc(ReconciliationRun.executed_at)).first()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No reconciliation run found.")

    # Calculate adjusted totals and unexplained variance
    dup_plat = db.query(func.sum(ReconciliationItem.platform_amount)).filter(
        ReconciliationItem.run_id == latest_run.run_id,
        ReconciliationItem.status == "DUPLICATE_PLATFORM"
    ).scalar() or 0

    dup_bank = db.query(func.sum(ReconciliationItem.bank_amount)).filter(
        ReconciliationItem.run_id == latest_run.run_id,
        ReconciliationItem.status == "DUPLICATE_BANK"
    ).scalar() or 0

    orphan_refund = db.query(func.sum(ReconciliationItem.bank_amount)).filter(
        ReconciliationItem.run_id == latest_run.run_id,
        ReconciliationItem.status == "ORPHAN_REFUND"
    ).scalar() or 0

    adjusted_platform = float(latest_run.total_platform_volume) - float(latest_run.timing_diff_amount) - float(dup_plat)
    adjusted_bank = float(latest_run.total_bank_volume) - float(dup_bank) - float(orphan_refund)
    unexplained = round(adjusted_platform - adjusted_bank, 2)
    if abs(unexplained) < 0.005:
        unexplained = 0.0

    waterfall = [
        {"step": "1. Platform Ledger Gross", "amount": float(latest_run.total_platform_volume), "type": "base"},
        {"step": "2. Less: Settled Next Month (Timing)", "amount": -float(latest_run.timing_diff_amount), "type": "deduction"},
        {"step": "3. Less: Platform Duplicate Retries", "amount": -float(dup_plat), "type": "deduction"},
        {"step": "4. Adjusted Platform Book", "amount": round(adjusted_platform, 2), "type": "subtotal"},
        {"step": "5. Bank Statement Gross", "amount": float(latest_run.total_bank_volume), "type": "base"},
        {"step": "6. Less: Bank Duplicate Payouts", "amount": -float(dup_bank), "type": "deduction"},
        {"step": "7. Add Back: Orphan Bank Debits (No Sale)", "amount": float(abs(orphan_refund)), "type": "deduction"},
        {"step": "8. Adjusted Bank Book", "amount": round(adjusted_bank, 2), "type": "subtotal"},
        {"step": "9. Net Unexplained Discrepancy", "amount": unexplained, "type": "final"}
    ]

    return {
        "run": ReconciliationRunSchema.model_validate(latest_run),
        "waterfall": waterfall,
        "unexplained_variance": unexplained
    }

@app.get("/api/reconcile/gaps-summary")
def get_gaps_summary(db: Session = Depends(get_db)):
    """
    Get detailed breakdown of the 4 planted gap types with root cause analysis,
    accounting impact, and sample items.
    """
    latest_run = db.query(ReconciliationRun).order_by(desc(ReconciliationRun.executed_at)).first()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No reconciliation run found.")

    run_id = latest_run.run_id

    # 1. Timing Differences
    timing_items = db.query(ReconciliationItem).filter(
        ReconciliationItem.run_id == run_id,
        ReconciliationItem.status == "TIMING_DIFFERENCE"
    ).all()
    timing_total = sum(float(i.platform_amount or 0) for i in timing_items)

    # 2. Rounding Drift
    rounding_items = db.query(ReconciliationItem).filter(
        ReconciliationItem.run_id == run_id,
        ReconciliationItem.status == "ROUNDING_VARIANCE"
    ).all()
    rounding_fee_drift = sum(float(i.fee_variance or 0) for i in rounding_items)

    # 3. Duplicates
    dup_bank_items = db.query(ReconciliationItem).filter(
        ReconciliationItem.run_id == run_id,
        ReconciliationItem.status == "DUPLICATE_BANK"
    ).all()
    dup_plat_items = db.query(ReconciliationItem).filter(
        ReconciliationItem.run_id == run_id,
        ReconciliationItem.status == "DUPLICATE_PLATFORM"
    ).all()
    dup_bank_total = sum(float(i.bank_amount or 0) for i in dup_bank_items)
    dup_plat_total = sum(float(i.platform_amount or 0) for i in dup_plat_items)

    # 4. Orphan Refunds
    orphan_items = db.query(ReconciliationItem).filter(
        ReconciliationItem.run_id == run_id,
        ReconciliationItem.status == "ORPHAN_REFUND"
    ).all()
    orphan_total = sum(float(i.bank_amount or 0) for i in orphan_items)

    return {
        "run_id": run_id,
        "gaps": {
            "timing_differences": {
                "title": "Timing Differences (Next-Month Settlement)",
                "category": "TIMING_DIFFERENCE",
                "count": len(timing_items),
                "total_amount": round(timing_total, 2),
                "financial_impact": "Cash in Transit / Clearing Asset (Not lost, delayed deposit)",
                "root_cause": (
                    "Transactions processed in the final 48 hours of October settled 1-3 business "
                    "days later in early November. Platform recognized revenue in October, but bank statement "
                    "recorded funds in November."
                ),
                "accounting_solution": "Record Month-End Balance Sheet Adjusting Journal Entry: Debit 'Cash in Transit', Credit 'Undeposited Funds'.",
                "sample_items": [ReconciliationItemSchema.model_validate(i) for i in timing_items[:10]]
            },
            "rounding_differences": {
                "title": "Cumulative Rounding Drift (Sub-cent Discrepancies)",
                "category": "ROUNDING_DIFFERENCE",
                "count": len(rounding_items),
                "total_drift_amount": round(rounding_fee_drift, 4),
                "financial_impact": "Expense under-reporting across aggregate transaction batches",
                "root_cause": (
                    "The platform calculates merchant processing fees on a per-transaction basis using standard "
                    "round-half-up (2 decimals), while acquiring banks calculate tiered interchange with 4 decimal "
                    "places and batch-aggregate before rounding. Individual transactions show negligible $0.00–$0.01 "
                    "variances, but across hundreds of transactions, cumulative variance totals significant dollars."
                ),
                "accounting_solution": "Post aggregate fee variance journal entry to 'Bank Processing Fee Expense' to reconcile GL to bank statement.",
                "sample_items": [ReconciliationItemSchema.model_validate(i) for i in rounding_items[:10]]
            },
            "duplicate_entries": {
                "title": "Duplicate Entries (Bank Over-Payout & Platform Retries)",
                "category": "DUPLICATE_ENTRY",
                "count": len(dup_bank_items) + len(dup_plat_items),
                "bank_duplicates_count": len(dup_bank_items),
                "bank_duplicates_amount": round(dup_bank_total, 2),
                "platform_duplicates_count": len(dup_plat_items),
                "platform_duplicates_amount": round(dup_plat_total, 2),
                "financial_impact": "Bank over-credited cash payout; Platform overstated revenue on retry",
                "root_cause": (
                    "1) Bank duplicate payout: Batch processing re-transmission caused bank to settle a transaction twice. "
                    "2) Platform duplicate charge: Checkout network timeout caused a retry that created a duplicate order row, "
                    "though payment gateway idempotency prevented the card from being billed twice."
                ),
                "accounting_solution": "Initiate processor clawback for bank over-credit; void redundant internal platform ledger transaction.",
                "sample_bank_items": [ReconciliationItemSchema.model_validate(i) for i in dup_bank_items],
                "sample_platform_items": [ReconciliationItemSchema.model_validate(i) for i in dup_plat_items]
            },
            "orphan_refunds": {
                "title": "Orphan Refunds (Bank Debits Without Platform Charges)",
                "category": "ORPHAN_REFUND",
                "count": len(orphan_items),
                "total_amount": round(orphan_total, 2),
                "financial_impact": "Unaccounted cash outflow / unauthorized refund debit",
                "root_cause": (
                    "Bank statement records refund and dispute debits, but no matching transaction exists in "
                    "the platform's ledger. Typically caused by manual customer refunds executed directly inside "
                    "the processor portal without webhook capture, or legacy chargebacks."
                ),
                "accounting_solution": "Investigate payment processor terminal logs, match dispute references, and record manual adjusting debit to Dispute Loss Expense.",
                "sample_items": [ReconciliationItemSchema.model_validate(i) for i in orphan_items]
            }
        }
    }

@app.get("/api/reconcile/items")
def get_reconciliation_items(
    status: Optional[str] = Query(None, description="Filter by status (MATCHED, TIMING_DIFFERENCE, etc.)"),
    category: Optional[str] = Query(None, description="Filter by discrepancy category"),
    search: Optional[str] = Query(None, description="Search txn ID, settlement ID, or notes"),
    gaps_only: bool = Query(False, description="Filter to only discrepancy items"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Query reconciliation items with rich filtering and pagination."""
    latest_run = db.query(ReconciliationRun).order_by(desc(ReconciliationRun.executed_at)).first()
    if not latest_run:
        return {"items": [], "total": 0}

    query = db.query(ReconciliationItem).filter(ReconciliationItem.run_id == latest_run.run_id)

    if gaps_only:
        query = query.filter(ReconciliationItem.status != "MATCHED")

    if status:
        query = query.filter(ReconciliationItem.status == status)

    if category:
        query = query.filter(ReconciliationItem.discrepancy_category == category)

    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            or_(
                ReconciliationItem.internal_txn_id.ilike(search_filter),
                ReconciliationItem.settlement_id.ilike(search_filter),
                ReconciliationItem.root_cause_explanation.ilike(search_filter)
            )
        )

    total_count = query.count()
    items = query.order_by(desc(ReconciliationItem.amount_variance)).offset(offset).limit(limit).all()

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "items": [ReconciliationItemSchema.model_validate(i) for i in items]
    }

@app.get("/api/transactions")
def list_transactions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """List platform internal transactions."""
    query = db.query(InternalTransaction)
    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(
                InternalTransaction.transaction_id.ilike(term),
                InternalTransaction.order_id.ilike(term),
                InternalTransaction.customer_id.ilike(term)
            )
        )
    total = query.count()
    records = query.order_by(desc(InternalTransaction.timestamp)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [InternalTransactionSchema.model_validate(r) for r in records]
    }

@app.get("/api/settlements")
def list_settlements(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """List bank settlements."""
    query = db.query(BankSettlement)
    if search:
        term = f"%{search}%"
        query = query.filter(
            or_(
                BankSettlement.settlement_id.ilike(term),
                BankSettlement.internal_txn_id.ilike(term),
                BankSettlement.bank_batch_id.ilike(term)
            )
        )
    total = query.count()
    records = query.order_by(desc(BankSettlement.settlement_date)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "items": [BankSettlementSchema.model_validate(r) for r in records]
    }

@app.post("/api/audit/action", response_model=AuditActionSchema)
def record_audit_action(action: AuditActionCreate, db: Session = Depends(get_db)):
    """Record an auditor note or status resolution on a discrepancy."""
    audit_rec = AuditAction(
        run_id=action.run_id,
        item_id=action.item_id,
        action_type=action.action_type,
        performed_by=action.performed_by,
        notes=action.notes
    )
    db.add(audit_rec)

    if action.item_id:
        item = db.query(ReconciliationItem).filter(ReconciliationItem.id == action.item_id).first()
        if item:
            item.investigation_status = "RESOLVED"
            item.resolution_notes = action.notes

    db.commit()
    db.refresh(audit_rec)
    return AuditActionSchema.model_validate(audit_rec)

@app.get("/api/audit/logs", response_model=List[AuditActionSchema])
def get_audit_logs(run_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    query = db.query(AuditAction)
    if run_id:
        query = query.filter(AuditAction.run_id == run_id)
    logs = query.order_by(desc(AuditAction.created_at)).limit(100).all()
    return [AuditActionSchema.model_validate(l) for l in logs]

@app.get("/api/export")
def export_reconciliation_csv(db: Session = Depends(get_db)):
    """Export all reconciliation results as CSV."""
    latest_run = db.query(ReconciliationRun).order_by(desc(ReconciliationRun.executed_at)).first()
    if not latest_run:
        raise HTTPException(status_code=404, detail="No reconciliation run to export.")

    items = db.query(ReconciliationItem).filter(ReconciliationItem.run_id == latest_run.run_id).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Item ID", "Status", "Category", "Platform Txn ID", "Bank Settlement ID",
        "Platform Amount", "Bank Amount", "Amount Variance", "Platform Fee", "Bank Fee",
        "Fee Variance", "Platform Date", "Settlement Date", "Lag Days", "Root Cause", "Action"
    ])

    for i in items:
        writer.writerow([
            i.id,
            i.status,
            i.discrepancy_category or "",
            i.internal_txn_id or "",
            i.settlement_id or "",
            float(i.platform_amount or 0),
            float(i.bank_amount or 0),
            float(i.amount_variance or 0),
            float(i.platform_fee or 0),
            float(i.bank_fee or 0),
            float(i.fee_variance or 0),
            i.platform_date.strftime("%Y-%m-%d %H:%M:%S") if i.platform_date else "",
            i.settlement_date.strftime("%Y-%m-%d %H:%M:%S") if i.settlement_date else "",
            i.timing_lag_days or "",
            i.root_cause_explanation or "",
            i.recommended_action or ""
        ])

    csv_data = output.getvalue()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=reconciliation_{latest_run.run_id}.csv"}
    )
