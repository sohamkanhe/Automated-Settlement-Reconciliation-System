"""
Automated Settlement Reconciliation System - Test Data Generator
Generates realistic payments platform transactions and bank settlement records
with precisely planted reconciliation gaps:
1. Timing difference (transactions settled in the following month)
2. Rounding difference that only shows when summed (sub-cent cumulative drift)
3. Duplicate entry in one dataset (bank duplicate and platform duplicate)
4. Refund with no matching original transaction (orphan refund)
"""

import sys
import random
import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_EVEN
from typing import List, Dict, Tuple, Any

MIN_TRANSACTIONS = 300

def round_cur(val: float or Decimal, places: int = 2) -> Decimal:
    """Format decimal to given decimal places using standard financial rounding."""
    d = Decimal(str(val))
    return d.quantize(Decimal('0.' + '0' * places), rounding=ROUND_HALF_UP)

def generate_reconciliation_datasets(
    num_transactions: int = 1200,
    month: int = 10,
    year: int = 2026,
    seed: int = 42
) -> Dict[str, Any]:
    """
    Generates internal transactions for month M (default October 2026),
    and bank settlement records (including late settlements into November 2026).
    Plants the 4 required discrepancy types with deterministic, easily traceable IDs.
    """
    if num_transactions < MIN_TRANSACTIONS:
        raise ValueError(f"At least {MIN_TRANSACTIONS} transactions are required")
    random.seed(seed)
    
    start_date = datetime.datetime(year, month, 1, 0, 0, 0)
    end_date = datetime.datetime(year, month, 31, 23, 59, 59)
    
    payment_methods = ["CREDIT_CARD", "DEBIT_CARD", "APPLE_PAY", "GOOGLE_PAY", "ACH_TRANSFER"]
    currencies = ["USD"]
    
    internal_transactions: List[Dict[str, Any]] = []
    bank_settlements: List[Dict[str, Any]] = []
    
    planted_gaps_meta = {
        "timing_differences": [],
        "rounding_differences": {
            "count": 0,
            "platform_cumulative_fee": Decimal("0.00"),
            "bank_cumulative_fee": Decimal("0.00"),
            "total_drift": Decimal("0.00"),
            "affected_txn_ids": []
        },
        "duplicates": {
            "bank_duplicates": [],
            "platform_duplicates": []
        },
        "orphan_refunds": []
    }
    
    # -------------------------------------------------------------
    # 1. BASELINE MATCHED TRANSACTIONS (~1000 txns across the month)
    # -------------------------------------------------------------
    base_txn_count = num_transactions - 60
    
    for i in range(1, base_txn_count + 1):
        txn_id = f"TXN_{year}{month:02d}_{i:05d}"
        order_id = f"ORD_{year}{month:02d}_{i:05d}"
        customer_id = f"CUST_{random.randint(1000, 9999)}"
        
        day_offset = random.uniform(0, 27)
        txn_time = start_date + datetime.timedelta(days=day_offset, seconds=random.randint(0, 86399))
        
        amount = round_cur(random.uniform(15.00, 480.00))
        fee_raw = (amount * Decimal("0.029")) + Decimal("0.30")
        fee_expected = round_cur(fee_raw, 2)
        net_expected = amount - fee_expected
        
        method = random.choice(payment_methods)
        ref_id = f"REF_GW_{year}{month:02d}_{i:05d}"
        
        internal_transactions.append({
            "transaction_id": txn_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": float(amount),
            "fee_expected": float(fee_expected),
            "net_expected": float(net_expected),
            "currency": "USD",
            "type": "PAYMENT",
            "status": "SUCCESS",
            "payment_method": method,
            "reference_id": ref_id,
            "notes": "Standard processed payment"
        })
        
        settle_lag_hours = random.randint(18, 48)
        settle_time = txn_time + datetime.timedelta(hours=settle_lag_hours)
        settle_id = f"SETTLE_{year}{month:02d}_{i:05d}"
        batch_id = f"BATCH_{settle_time.strftime('%Y%m%d')}_{random.randint(1, 4)}"
        
        bank_settlements.append({
            "settlement_id": settle_id,
            "bank_batch_id": batch_id,
            "internal_txn_id": txn_id,
            "settlement_date": settle_time.strftime("%Y-%m-%d %H:%M:%S"),
            "gross_amount": float(amount),
            "bank_fee": float(fee_expected),
            "net_amount": float(net_expected),
            "currency": "USD",
            "type": "SETTLEMENT",
            "bank_reference": f"ACH_CR_{settle_time.strftime('%Y%m%d')}_{i:05d}",
            "notes": "Matched normal settlement"
        })

    # -------------------------------------------------------------
    # GAP TYPE 1: TRANSACTION THAT SETTLED THE FOLLOWING MONTH
    # (Timing / Cut-off Difference)
    # -------------------------------------------------------------
    timing_gap_count = 12
    curr_idx = base_txn_count + 1
    
    for _ in range(timing_gap_count):
        txn_id = f"TXN_{year}{month:02d}_{curr_idx:05d}"
        order_id = f"ORD_{year}{month:02d}_{curr_idx:05d}"
        customer_id = f"CUST_{random.randint(1000, 9999)}"
        
        late_hours = random.uniform(0, 31)
        txn_time = datetime.datetime(year, month, 30, 16, 0, 0) + datetime.timedelta(hours=late_hours)
        
        amount = round_cur(random.uniform(250.00, 1850.00))
        fee_expected = round_cur((amount * Decimal("0.029")) + Decimal("0.30"), 2)
        net_expected = amount - fee_expected
        ref_id = f"REF_GW_{year}{month:02d}_{curr_idx:05d}"
        
        internal_transactions.append({
            "transaction_id": txn_id,
            "order_id": order_id,
            "customer_id": customer_id,
            "timestamp": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": float(amount),
            "fee_expected": float(fee_expected),
            "net_expected": float(net_expected),
            "currency": "USD",
            "type": "PAYMENT",
            "status": "SUCCESS",
            "payment_method": "CREDIT_CARD",
            "reference_id": ref_id,
            "notes": "Late month transaction (Settled in November)"
        })
        
        nov_day = random.choice([2, 3])
        settle_time = datetime.datetime(year, 11, nov_day, random.randint(3, 9), random.randint(0, 59))
        settle_id = f"SETTLE_{year}11_{curr_idx:05d}"
        batch_id = f"BATCH_202611{nov_day:02d}_01"
        
        bank_settlements.append({
            "settlement_id": settle_id,
            "bank_batch_id": batch_id,
            "internal_txn_id": txn_id,
            "settlement_date": settle_time.strftime("%Y-%m-%d %H:%M:%S"),
            "gross_amount": float(amount),
            "bank_fee": float(fee_expected),
            "net_amount": float(net_expected),
            "currency": "USD",
            "type": "SETTLEMENT",
            "bank_reference": f"ACH_CR_202611{nov_day:02d}_{curr_idx:05d}",
            "notes": "Next-month settlement"
        })
        
        planted_gaps_meta["timing_differences"].append({
            "transaction_id": txn_id,
            "txn_date": txn_time.strftime("%Y-%m-%d %H:%M:%S"),
            "settlement_date": settle_time.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": float(amount),
            "explanation": "Processed late October, funds deposited by bank early November."
        })
        curr_idx += 1

    # -------------------------------------------------------------
    # GAP TYPE 2: ROUNDING DIFFERENCE THAT ONLY SHOWS WHEN SUMMED
    # (Sub-cent / Fractional Fee Aggregation Drift)
    # -------------------------------------------------------------
    rounding_txns_count = min(350, base_txn_count)
    sample_indices = random.sample(range(base_txn_count), rounding_txns_count)
    
    total_plat_fee = Decimal("0.00")
    total_bank_fee = Decimal("0.00")
    
    for idx in sample_indices:
        itxn = internal_transactions[idx]
        b_settle = bank_settlements[idx]
        
        amt = Decimal(str(itxn["amount"]))
        p_fee = Decimal(str(itxn["fee_expected"]))
        
        sub_cent_discount = Decimal(str(round(random.uniform(0.008, 0.022), 4)))
        b_fee = (p_fee - sub_cent_discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
        
        b_settle["bank_fee"] = float(b_fee)
        b_settle["net_amount"] = float(amt - b_fee)
        b_settle["notes"] = "Subject to tiered interchange aggregation rounding"
        
        total_plat_fee += p_fee
        total_bank_fee += b_fee
        planted_gaps_meta["rounding_differences"]["affected_txn_ids"].append(itxn["transaction_id"])
    
    planted_gaps_meta["rounding_differences"]["count"] = rounding_txns_count
    planted_gaps_meta["rounding_differences"]["platform_cumulative_fee"] = float(total_plat_fee)
    planted_gaps_meta["rounding_differences"]["bank_cumulative_fee"] = float(total_bank_fee)
    planted_gaps_meta["rounding_differences"]["total_drift"] = float(total_plat_fee - total_bank_fee)

    # -------------------------------------------------------------
    # GAP TYPE 3: DUPLICATE ENTRY IN ONE DATASET
    # -------------------------------------------------------------
    bank_dup_source_txn = internal_transactions[14]
    dup_bank_settle = {
        "settlement_id": f"SETTLE_DUP_BATCH_{year}{month:02d}_9901",
        "bank_batch_id": f"BATCH_{year}{month:02d}18_ERR_DUP",
        "internal_txn_id": bank_dup_source_txn["transaction_id"],
        "settlement_date": "2026-10-18 11:30:00",
        "gross_amount": bank_dup_source_txn["amount"],
        "bank_fee": bank_dup_source_txn["fee_expected"],
        "net_amount": bank_dup_source_txn["net_expected"],
        "currency": "USD",
        "type": "SETTLEMENT",
        "bank_reference": f"ACH_CR_DUP_9901",
        "notes": "DUPLICATE: Bank batch retransmission caused duplicate payout"
    }
    bank_settlements.append(dup_bank_settle)
    planted_gaps_meta["duplicates"]["bank_duplicates"].append({
        "original_transaction_id": bank_dup_source_txn["transaction_id"],
        "duplicate_settlement_id": dup_bank_settle["settlement_id"],
        "amount": bank_dup_source_txn["amount"],
        "explanation": "Bank batch processed twice. Duplicate credit of funds to company account."
    })
    
    plat_orig_txn = internal_transactions[27]
    plat_dup_txn = {
        "transaction_id": f"TXN_{year}{month:02d}_DUP_8802",
        "order_id": plat_orig_txn["order_id"],
        "customer_id": plat_orig_txn["customer_id"],
        "timestamp": plat_orig_txn["timestamp"],
        "amount": plat_orig_txn["amount"],
        "fee_expected": plat_orig_txn["fee_expected"],
        "net_expected": plat_orig_txn["net_expected"],
        "currency": "USD",
        "type": "PAYMENT",
        "status": "SUCCESS",
        "payment_method": plat_orig_txn["payment_method"],
        "reference_id": f"REF_GW_DUP_RETRY_8802",
        "notes": "DUPLICATE: Platform retry webhook registered identical charge"
    }
    internal_transactions.append(plat_dup_txn)
    planted_gaps_meta["duplicates"]["platform_duplicates"].append({
        "original_transaction_id": plat_orig_txn["transaction_id"],
        "duplicate_transaction_id": plat_dup_txn["transaction_id"],
        "order_id": plat_orig_txn["order_id"],
        "amount": plat_orig_txn["amount"],
        "explanation": "Platform recorded duplicate entry on checkout retry, but bank only processed once."
    })

    # -------------------------------------------------------------
    # GAP TYPE 4: REFUND WITH NO MATCHING ORIGINAL TRANSACTION
    # (Orphan / Unmatched Refund)
    # -------------------------------------------------------------
    orphan_refunds_data = [
        {"id": "SETTLE_REFUND_ORPHAN_01", "amount": -320.00, "fee": 15.00, "date": "2026-10-14 14:10:00", "ref": "CHARGEBACK_FORCED_01"},
        {"id": "SETTLE_REFUND_ORPHAN_02", "amount": -785.50, "fee": 15.00, "date": "2026-10-22 09:45:00", "ref": "PORTAL_DIRECT_REFUND_02"},
        {"id": "SETTLE_REFUND_ORPHAN_03", "amount": -142.25, "fee": 0.00, "date": "2026-10-27 16:20:00", "ref": "DISPUTE_REVERSAL_03"},
    ]
    
    for rfd in orphan_refunds_data:
        orphan_settle = {
            "settlement_id": rfd["id"],
            "bank_batch_id": f"BATCH_{year}{month:02d}20_REF",
            "internal_txn_id": None,
            "settlement_date": rfd["date"],
            "gross_amount": rfd["amount"],
            "bank_fee": rfd["fee"],
            "net_amount": rfd["amount"] - rfd["fee"],
            "currency": "USD",
            "type": "REFUND",
            "bank_reference": rfd["ref"],
            "notes": "ORPHAN: Bank refund debit with no matching platform transaction"
        }
        bank_settlements.append(orphan_settle)
        planted_gaps_meta["orphan_refunds"].append({
            "settlement_id": rfd["id"],
            "amount": rfd["amount"],
            "fee": rfd["fee"],
            "date": rfd["date"],
            "bank_reference": rfd["ref"],
            "explanation": "Bank debited account for refund/dispute, but platform has no record of the original transaction."
        })

    return {
        "internal_transactions": internal_transactions,
        "bank_settlements": bank_settlements,
        "metadata": {
            "period": f"{year}-{month:02d}",
            "generated_at": datetime.datetime.now().isoformat(),
            "internal_count": len(internal_transactions),
            "bank_count": len(bank_settlements),
            "planted_gaps": planted_gaps_meta
        }
    }
