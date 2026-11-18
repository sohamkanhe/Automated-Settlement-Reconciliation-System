import uuid
import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import (
    InternalTransaction, BankSettlement, ReconciliationRun,
    ReconciliationItem, AuditAction
)

def d_cur(val: Any) -> Decimal:
    """Safe decimal converter."""
    if val is None:
        return Decimal("0.00")
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def d_fee(val: Any) -> Decimal:
    """Safe fee decimal converter (4 decimals)."""
    if val is None:
        return Decimal("0.0000")
    return Decimal(str(val)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

class ReconciliationEngine:
    """
    Deterministic multi-pass reconciliation engine designed to identify:
    1. Timing differences (transactions settled in the following month)
    2. Rounding differences that only reveal when summed
    3. Duplicate entries in either dataset (bank over-settlements & platform ghost entries)
    4. Refunds with no matching original transaction (orphan debits)
    """

    def __init__(self, db: Session):
        self.db = db

    def run_reconciliation(
        self,
        year: int = 2026,
        month: int = 10,
        run_id: Optional[str] = None
    ) -> Dict[str, Any]:
        if not run_id:
            run_id = f"RECON_{year}{month:02d}_{uuid.uuid4().hex[:8].upper()}"

        period_start = datetime.datetime(year, month, 1, 0, 0, 0)
        # End of selected month
        if month == 12:
            period_end = datetime.datetime(year + 1, 1, 1, 0, 0, 0) - datetime.timedelta(seconds=1)
            next_month_start = datetime.datetime(year + 1, 1, 1, 0, 0, 0)
            next_month_end = datetime.datetime(year + 1, 1, 15, 23, 59, 59)
        else:
            period_end = datetime.datetime(year, month + 1, 1, 0, 0, 0) - datetime.timedelta(seconds=1)
            next_month_start = datetime.datetime(year, month + 1, 1, 0, 0, 0)
            next_month_end = datetime.datetime(year, month + 1, 15, 23, 59, 59)

        # 1. Fetch Month Internal Transactions
        internal_txns = self.db.query(InternalTransaction).filter(
            InternalTransaction.timestamp >= period_start,
            InternalTransaction.timestamp <= period_end
        ).all()

        # 2. Fetch Month Bank Settlements
        month_settlements = self.db.query(BankSettlement).filter(
            BankSettlement.settlement_date >= period_start,
            BankSettlement.settlement_date <= period_end
        ).all()

        # 3. Fetch Next Month Bank Settlements (for Cross-Month Timing Gap Resolution)
        next_month_settlements = self.db.query(BankSettlement).filter(
            BankSettlement.settlement_date >= next_month_start,
            BankSettlement.settlement_date <= next_month_end
        ).all()

        # Build index lookups
        # Map internal_txn_id -> list of bank settlements in month
        month_settle_by_txn_id: Dict[str, List[BankSettlement]] = {}
        for s in month_settlements:
            if s.internal_txn_id:
                month_settle_by_txn_id.setdefault(s.internal_txn_id, []).append(s)

        # Map internal_txn_id -> list of next-month bank settlements
        next_month_settle_by_txn_id: Dict[str, List[BankSettlement]] = {}
        for s in next_month_settlements:
            if s.internal_txn_id:
                next_month_settle_by_txn_id.setdefault(s.internal_txn_id, []).append(s)

        # Group internal txns by order_id to detect platform duplicates
        order_to_internal_txns: Dict[str, List[InternalTransaction]] = {}
        for itxn in internal_txns:
            if itxn.order_id:
                order_to_internal_txns.setdefault(itxn.order_id, []).append(itxn)

        # Keep track of handled settlement IDs in current month
        handled_settlement_ids = set()
        
        # Result buckets
        items_to_save: List[ReconciliationItem] = []

        total_platform_vol = Decimal("0.00")
        total_platform_fees = Decimal("0.00")
        total_platform_net = Decimal("0.00")

        total_bank_vol = Decimal("0.00")
        total_bank_fees = Decimal("0.00")
        total_bank_net = Decimal("0.00")

        # Sum current month bank settlements
        for s in month_settlements:
            total_bank_vol += d_cur(s.gross_amount)
            total_bank_fees += d_fee(s.bank_fee)
            total_bank_net += d_cur(s.net_amount)

        # Metrics for gaps
        timing_diff_count = 0
        timing_diff_amount = Decimal("0.00")

        rounding_drift_count = 0
        rounding_drift_amount = Decimal("0.00")

        duplicate_count = 0
        duplicate_amount = Decimal("0.00")

        orphan_refund_count = 0
        orphan_refund_amount = Decimal("0.00")

        clean_matched_count = 0
        unmatched_platform_count = 0

        # PASS 1 & 2: Process all internal transactions
        for itxn in internal_txns:
            p_amt = d_cur(itxn.amount)
            p_fee = d_fee(itxn.fee_expected)
            p_net = d_cur(itxn.net_expected)

            total_platform_vol += p_amt
            total_platform_fees += p_fee
            total_platform_net += p_net

            # Check 1: Is this transaction part of a platform duplicate?
            order_group = order_to_internal_txns.get(itxn.order_id, [])
            if len(order_group) > 1 and itxn.transaction_id != order_group[0].transaction_id:
                # This is the secondary/duplicate platform record
                item = ReconciliationItem(
                    run_id=run_id,
                    internal_txn_id=itxn.transaction_id,
                    settlement_id=None,
                    status="DUPLICATE_PLATFORM",
                    discrepancy_category="DUPLICATE_ENTRY",
                    platform_amount=p_amt,
                    bank_amount=Decimal("0.00"),
                    amount_variance=p_amt,
                    platform_fee=p_fee,
                    bank_fee=Decimal("0.00"),
                    fee_variance=p_fee,
                    platform_date=itxn.timestamp,
                    settlement_date=None,
                    timing_lag_days=None,
                    root_cause_explanation=(
                        f"Platform idempotency/retry issue: Order {itxn.order_id} recorded twice "
                        f"in internal ledger ({order_group[0].transaction_id} and {itxn.transaction_id}). "
                        f"The bank processor deduplicated and only billed customer once."
                    ),
                    recommended_action="Void duplicate internal ledger row. Reconcile Order to single charge.",
                    financial_impact_category="OVERSTATED_PLATFORM_REVENUE"
                )
                items_to_save.append(item)
                duplicate_count += 1
                duplicate_amount += p_amt
                continue

            # Check 2: Look for settlements in the current month
            matching_settles = month_settle_by_txn_id.get(itxn.transaction_id, [])

            if len(matching_settles) == 1:
                settle = matching_settles[0]
                handled_settlement_ids.add(settle.settlement_id)
                b_amt = d_cur(settle.gross_amount)
                b_fee = d_fee(settle.bank_fee)
                amt_var = p_amt - b_amt
                fee_var = p_fee - b_fee

                # Check for Rounding / Sub-cent fee variance
                if amt_var == Decimal("0.00") and fee_var != Decimal("0.0000"):
                    # Micro-rounding / interchange fee calculation disparity
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=settle.settlement_id,
                        status="ROUNDING_VARIANCE",
                        discrepancy_category="ROUNDING_DIFFERENCE",
                        platform_amount=p_amt,
                        bank_amount=b_amt,
                        amount_variance=Decimal("0.00"),
                        platform_fee=p_fee,
                        bank_fee=b_fee,
                        fee_variance=fee_var,
                        platform_date=itxn.timestamp,
                        settlement_date=settle.settlement_date,
                        timing_lag_days=(settle.settlement_date - itxn.timestamp).days,
                        root_cause_explanation=(
                            f"Fee calculation discrepancy: Platform estimated fee ${p_fee} vs Bank deducted ${b_fee} "
                            f"(variance of ${fee_var:+.4f}). Sub-cent rounding only noticeable when summed."
                        ),
                        recommended_action="Post fee rounding adjustment to General Ledger bank charges.",
                        financial_impact_category="FEE_EXPENSE_VARIANCE"
                    ))
                    rounding_drift_count += 1
                    rounding_drift_amount += fee_var

                elif amt_var == Decimal("0.00") and fee_var == Decimal("0.0000"):
                    # Perfect Clean Match
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=settle.settlement_id,
                        status="MATCHED",
                        discrepancy_category="CLEAN_MATCH",
                        platform_amount=p_amt,
                        bank_amount=b_amt,
                        amount_variance=Decimal("0.00"),
                        platform_fee=p_fee,
                        bank_fee=b_fee,
                        fee_variance=Decimal("0.0000"),
                        platform_date=itxn.timestamp,
                        settlement_date=settle.settlement_date,
                        timing_lag_days=(settle.settlement_date - itxn.timestamp).days,
                        root_cause_explanation="Exact 100% match on Gross Amount, Processing Fees, and Reference IDs.",
                        recommended_action="No action required. Reconciled clean.",
                        financial_impact_category="NONE"
                    ))
                    clean_matched_count += 1

                else:
                    # Amount Mismatch
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=settle.settlement_id,
                        status="AMOUNT_MISMATCH",
                        discrepancy_category="AMOUNT_DISCREPANCY",
                        platform_amount=p_amt,
                        bank_amount=b_amt,
                        amount_variance=amt_var,
                        platform_fee=p_fee,
                        bank_fee=b_fee,
                        fee_variance=fee_var,
                        platform_date=itxn.timestamp,
                        settlement_date=settle.settlement_date,
                        timing_lag_days=(settle.settlement_date - itxn.timestamp).days,
                        root_cause_explanation=f"Gross amount mismatch: Platform recorded ${p_amt} but Bank settled ${b_amt}.",
                        recommended_action="Investigate partial capture or customer invoice adjustment.",
                        financial_impact_category="PARTIAL_CAPTURE"
                    ))

            elif len(matching_settles) > 1:
                # Bank Duplicate! Multiple bank settlements for 1 internal transaction
                # Add primary match
                s_primary = matching_settles[0]
                handled_settlement_ids.add(s_primary.settlement_id)
                items_to_save.append(ReconciliationItem(
                    run_id=run_id,
                    internal_txn_id=itxn.transaction_id,
                    settlement_id=s_primary.settlement_id,
                    status="MATCHED",
                    discrepancy_category="CLEAN_MATCH",
                    platform_amount=p_amt,
                    bank_amount=d_cur(s_primary.gross_amount),
                    amount_variance=Decimal("0.00"),
                    platform_fee=p_fee,
                    bank_fee=d_fee(s_primary.bank_fee),
                    fee_variance=Decimal("0.0000"),
                    platform_date=itxn.timestamp,
                    settlement_date=s_primary.settlement_date,
                    timing_lag_days=(s_primary.settlement_date - itxn.timestamp).days,
                    root_cause_explanation="Primary matching settlement.",
                    recommended_action="Retain as primary reconciliation link.",
                    financial_impact_category="NONE"
                ))
                clean_matched_count += 1

                # Flag subsequent duplicate settlements
                for s_dup in matching_settles[1:]:
                    handled_settlement_ids.add(s_dup.settlement_id)
                    b_dup_amt = d_cur(s_dup.gross_amount)
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=s_dup.settlement_id,
                        status="DUPLICATE_BANK",
                        discrepancy_category="DUPLICATE_ENTRY",
                        platform_amount=Decimal("0.00"),
                        bank_amount=b_dup_amt,
                        amount_variance=-b_dup_amt,
                        platform_fee=Decimal("0.00"),
                        bank_fee=d_fee(s_dup.bank_fee),
                        fee_variance=-d_fee(s_dup.bank_fee),
                        platform_date=itxn.timestamp,
                        settlement_date=s_dup.settlement_date,
                        timing_lag_days=(s_dup.settlement_date - itxn.timestamp).days,
                        root_cause_explanation=(
                            f"Bank duplicate settlement: Batch error caused bank to deposit ${b_dup_amt} "
                            f"a second time for transaction {itxn.transaction_id} under settlement {s_dup.settlement_id}."
                        ),
                        recommended_action="Initiate processor clawback / debit correction to return excess payout.",
                        financial_impact_category="OVER_CREDITED_CASH"
                    ))
                    duplicate_count += 1
                    duplicate_amount += b_dup_amt

            else:
                # No settlement found in current month. Check next month for Timing Gap!
                next_month_settles = next_month_settle_by_txn_id.get(itxn.transaction_id, [])
                if next_month_settles:
                    s_next = next_month_settles[0]
                    lag = (s_next.settlement_date - itxn.timestamp).days
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=s_next.settlement_id,
                        status="TIMING_DIFFERENCE",
                        discrepancy_category="TIMING_DIFFERENCE",
                        platform_amount=p_amt,
                        bank_amount=Decimal("0.00"), # Zero received in October statement
                        amount_variance=p_amt,
                        platform_fee=p_fee,
                        bank_fee=Decimal("0.00"),
                        fee_variance=p_fee,
                        platform_date=itxn.timestamp,
                        settlement_date=s_next.settlement_date,
                        timing_lag_days=lag,
                        root_cause_explanation=(
                            f"Month-end Cut-off Timing Gap: Platform processed transaction on "
                            f"{itxn.timestamp.strftime('%Y-%m-%d %H:%M')}, but bank settled on "
                            f"{s_next.settlement_date.strftime('%Y-%m-%d %H:%M')} (Following Month). "
                            f"Funds confirmed arrived in early November statement ({s_next.settlement_id})."
                        ),
                        recommended_action="Accrue as 'Cash in Transit / Settlement Clearing' on October balance sheet.",
                        financial_impact_category="ASSET_IN_TRANSIT"
                    ))
                    timing_diff_count += 1
                    timing_diff_amount += p_amt
                else:
                    # Platform transaction completely unsettled
                    items_to_save.append(ReconciliationItem(
                        run_id=run_id,
                        internal_txn_id=itxn.transaction_id,
                        settlement_id=None,
                        status="UNSETTLED_INTERNAL",
                        discrepancy_category="UNSETTLED_TRANSACTION",
                        platform_amount=p_amt,
                        bank_amount=Decimal("0.00"),
                        amount_variance=p_amt,
                        platform_fee=p_fee,
                        bank_fee=Decimal("0.00"),
                        fee_variance=p_fee,
                        platform_date=itxn.timestamp,
                        settlement_date=None,
                        timing_lag_days=None,
                        root_cause_explanation="Platform recorded transaction but bank has no record of settlement.",
                        recommended_action="Verify gateway webhook logs and confirm processor payout status.",
                        financial_impact_category="MISSING_BANK_DEPOSIT"
                    ))
                    unmatched_platform_count += 1

        # PASS 3: Process remaining unhandled bank settlements in the current month
        for s in month_settlements:
            if s.settlement_id in handled_settlement_ids:
                continue

            b_amt = d_cur(s.gross_amount)
            b_fee = d_fee(s.bank_fee)

            # Check if this is an Orphan Refund (refund with no matching platform record)
            if s.type == "REFUND" or b_amt < Decimal("0.00"):
                items_to_save.append(ReconciliationItem(
                    run_id=run_id,
                    internal_txn_id=None,
                    settlement_id=s.settlement_id,
                    status="ORPHAN_REFUND",
                    discrepancy_category="ORPHAN_REFUND",
                    platform_amount=Decimal("0.00"),
                    bank_amount=b_amt,
                    amount_variance=-b_amt,
                    platform_fee=Decimal("0.00"),
                    bank_fee=b_fee,
                    fee_variance=-b_fee,
                    platform_date=None,
                    settlement_date=s.settlement_date,
                    timing_lag_days=None,
                    root_cause_explanation=(
                        f"Orphan Refund: Bank settled a debit of ${abs(b_amt)} on "
                        f"{s.settlement_date.strftime('%Y-%m-%d')} (Ref: {s.bank_reference}), "
                        f"but no original transaction or refund authorization exists in platform books."
                    ),
                    recommended_action="Audit processor portal for unauthorized manual refunds or merchant disputes.",
                    financial_impact_category="UNAUTHORIZED_OR_UNRECORDED_DEBIT"
                ))
                orphan_refund_count += 1
                orphan_refund_amount += b_amt

            else:
                # Unmatched credit deposit from bank
                items_to_save.append(ReconciliationItem(
                    run_id=run_id,
                    internal_txn_id=None,
                    settlement_id=s.settlement_id,
                    status="UNMATCHED_BANK",
                    discrepancy_category="UNMATCHED_DEPOSIT",
                    platform_amount=Decimal("0.00"),
                    bank_amount=b_amt,
                    amount_variance=-b_amt,
                    platform_fee=Decimal("0.00"),
                    bank_fee=b_fee,
                    fee_variance=-b_fee,
                    platform_date=None,
                    settlement_date=s.settlement_date,
                    timing_lag_days=None,
                    root_cause_explanation="Bank recorded deposit with no corresponding internal platform transaction.",
                    recommended_action="Trace bank batch trace ID to identify source payment or manual adjustment.",
                    financial_impact_category="UNRECORDED_DEPOSIT"
                ))

        # Gross & Net Variances
        gross_variance = total_platform_vol - total_bank_vol
        fee_variance = total_platform_fees - total_bank_fees
        net_variance = total_platform_net - total_bank_net

        total_analyzed = len(internal_txns)
        match_rate = Decimal("0.0")
        if total_analyzed > 0:
            match_rate = ((Decimal(str(clean_matched_count)) / Decimal(str(total_analyzed))) * Decimal("100.0")).quantize(Decimal("0.01"))

        # Save Reconciliation Run
        run_record = ReconciliationRun(
            run_id=run_id,
            period_start=period_start,
            period_end=period_end,
            total_platform_txns=len(internal_txns),
            total_platform_volume=total_platform_vol,
            total_platform_fees=total_platform_fees,
            total_platform_net=total_platform_net,
            total_bank_settlements=len(month_settlements),
            total_bank_volume=total_bank_vol,
            total_bank_fees=total_bank_fees,
            total_bank_net=total_bank_net,
            gross_variance=gross_variance,
            fee_variance=fee_variance,
            net_variance=net_variance,
            timing_diff_count=timing_diff_count,
            timing_diff_amount=timing_diff_amount,
            rounding_drift_count=rounding_drift_count,
            rounding_drift_amount=rounding_drift_amount,
            duplicate_count=duplicate_count,
            duplicate_amount=duplicate_amount,
            orphan_refund_count=orphan_refund_count,
            orphan_refund_amount=orphan_refund_amount,
            clean_matched_count=clean_matched_count,
            unmatched_platform_count=unmatched_platform_count,
            unmatched_bank_count=len(month_settlements) - len(handled_settlement_ids),
            match_rate_percentage=match_rate,
            status="COMPLETED"
        )

        # Build Financial Waterfall Reconciled Proof
        # Reconciled Net Explanations:
        # Platform Gross Volume
        # - Timing Differences (funds received in Nov)
        # - Duplicate Platform Transactions (phantom records)
        # = Adjusted Platform Volume (Gross sales that should have settled in October)
        #
        # Bank Gross Volume
        # - Bank Duplicate Over-credits
        # - Orphan Refunds (Debits subtracted by bank that had no platform charge)
        # = Adjusted Bank Volume (Gross sales actually settled by bank for October)
        #
        # Unexplained Variance: Should be exactly 0.00!
        plat_dup_total = sum(d_cur(i.platform_amount) for i in items_to_save if i.status == "DUPLICATE_PLATFORM")
        bank_dup_total = sum(d_cur(i.bank_amount) for i in items_to_save if i.status == "DUPLICATE_BANK")
        orphan_refund_total = sum(d_cur(i.bank_amount) for i in items_to_save if i.status == "ORPHAN_REFUND")

        adjusted_platform_vol = total_platform_vol - timing_diff_amount - plat_dup_total
        # orphan_refund_total is negative (e.g. -1247.75), so subtracting it adds back the debit
        adjusted_bank_vol = total_bank_vol - bank_dup_total - orphan_refund_total
        unexplained_variance = (adjusted_platform_vol - adjusted_bank_vol).quantize(Decimal("0.01"))

        result = {
            "run_id": run_id,
            "period": f"{year}-{month:02d}",
            "metrics": {
                "total_platform_txns": len(internal_txns),
                "total_platform_volume": float(total_platform_vol),
                "total_platform_fees": float(total_platform_fees),
                "total_platform_net": float(total_platform_net),
                "total_bank_settlements": len(month_settlements),
                "total_bank_volume": float(total_bank_vol),
                "total_bank_fees": float(total_bank_fees),
                "total_bank_net": float(total_bank_net),
                "gross_variance": float(gross_variance),
                "fee_variance": float(fee_variance),
                "net_variance": float(net_variance),
                "match_rate_percentage": float(match_rate),
                "unexplained_variance": float(unexplained_variance)
            },
            "planted_gaps_summary": {
                "timing_differences": {
                    "count": timing_diff_count,
                    "total_amount": float(timing_diff_amount),
                    "root_cause": "Month-end transactions settled T+2/T+3 days later in the following month (November).",
                    "accounting_treatment": "Recognize as Cash in Transit (Asset) on Month-End Balance Sheet."
                },
                "rounding_drift": {
                    "count": rounding_drift_count,
                    "total_drift_amount": float(rounding_drift_amount),
                    "root_cause": "Sub-cent pricing rounding variance between platform rate calculation and bank batch interchange fees.",
                    "accounting_treatment": "Post adjusting journal entry to General Ledger Bank Service Charges."
                },
                "duplicates": {
                    "count": duplicate_count,
                    "total_amount": float(duplicate_amount),
                    "root_cause": "Bank duplicate settlement payouts and platform checkout retry duplicate rows.",
                    "accounting_treatment": "Claw back duplicate bank payout from processor and void duplicate platform ledger row."
                },
                "orphan_refunds": {
                    "count": orphan_refund_count,
                    "total_amount": float(orphan_refund_amount),
                    "root_cause": "Bank settled refund debits with no matching sales transaction in platform database.",
                    "accounting_treatment": "Debit Chargeback Expense / investigate merchant portal dispute logs."
                }
            },
            "waterfall": [
                {"step": "1. Platform Gross Ledger", "amount": float(total_platform_vol), "type": "base"},
                {"step": "2. Less: Settled Following Month (Timing)", "amount": -float(timing_diff_amount), "type": "deduction"},
                {"step": "3. Less: Duplicate Platform Retries", "amount": -float(plat_dup_total), "type": "deduction"},
                {"step": "4. Adjusted Platform Book", "amount": float(adjusted_platform_vol), "type": "subtotal"},
                {"step": "5. Bank Gross Received", "amount": float(total_bank_vol), "type": "base"},
                {"step": "6. Less: Bank Duplicate Over-credits", "amount": -float(bank_dup_total), "type": "deduction"},
                {"step": "7. Add Back: Orphan Bank Debits (No Sale)", "amount": float(abs(orphan_refund_total)), "type": "deduction"},
                {"step": "8. Adjusted Bank Book", "amount": float(adjusted_bank_vol), "type": "subtotal"},
                {"step": "9. Net Unexplained Discrepancy", "amount": float(unexplained_variance), "type": "final"}
            ]
        }

        self.db.add(run_record)
        self.db.add_all(items_to_save)
        self.db.commit()
        return result
