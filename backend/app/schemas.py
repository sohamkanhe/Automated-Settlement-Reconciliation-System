from pydantic import BaseModel, ConfigDict, Field
from data_generator import MIN_TRANSACTIONS
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal

class InternalTransactionSchema(BaseModel):
    transaction_id: str
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    timestamp: datetime
    amount: float
    fee_expected: float
    net_expected: float
    currency: str = "USD"
    type: str = "PAYMENT"
    status: str = "SUCCESS"
    payment_method: Optional[str] = None
    reference_id: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class BankSettlementSchema(BaseModel):
    settlement_id: str
    bank_batch_id: Optional[str] = None
    internal_txn_id: Optional[str] = None
    settlement_date: datetime
    gross_amount: float
    bank_fee: float
    net_amount: float
    currency: str = "USD"
    type: str = "SETTLEMENT"
    bank_reference: Optional[str] = None
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ReconciliationItemSchema(BaseModel):
    id: int
    run_id: str
    internal_txn_id: Optional[str] = None
    settlement_id: Optional[str] = None
    status: str
    discrepancy_category: Optional[str] = None
    platform_amount: Optional[float] = None
    bank_amount: Optional[float] = None
    amount_variance: Optional[float] = None
    platform_fee: Optional[float] = None
    bank_fee: Optional[float] = None
    fee_variance: Optional[float] = None
    platform_date: Optional[datetime] = None
    settlement_date: Optional[datetime] = None
    timing_lag_days: Optional[int] = None
    root_cause_explanation: Optional[str] = None
    recommended_action: Optional[str] = None
    financial_impact_category: Optional[str] = None
    investigation_status: str
    resolution_notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class ReconciliationRunSchema(BaseModel):
    run_id: str
    period_start: datetime
    period_end: datetime
    total_platform_txns: int
    total_platform_volume: float
    total_platform_fees: float
    total_platform_net: float
    total_bank_settlements: int
    total_bank_volume: float
    total_bank_fees: float
    total_bank_net: float
    gross_variance: float
    fee_variance: float
    net_variance: float
    timing_diff_count: int
    timing_diff_amount: float
    rounding_drift_count: int
    rounding_drift_amount: float
    duplicate_count: int
    duplicate_amount: float
    orphan_refund_count: int
    orphan_refund_amount: float
    clean_matched_count: int
    unmatched_platform_count: int
    unmatched_bank_count: int
    match_rate_percentage: float
    status: str
    executed_at: datetime

    model_config = ConfigDict(from_attributes=True)

class AuditActionCreate(BaseModel):
    run_id: str
    item_id: Optional[int] = None
    action_type: str
    notes: str
    performed_by: Optional[str] = "Reconciliation Auditor"

class AuditActionSchema(BaseModel):
    id: int
    run_id: str
    item_id: Optional[int] = None
    action_type: str
    performed_by: str
    notes: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class GenerateDataRequest(BaseModel):
    num_transactions: int = Field(default=1200, ge=MIN_TRANSACTIONS)
    month: int = 10
    year: int = 2026
    seed: int = 42

class ReconcileRequest(BaseModel):
    month: int = 10
    year: int = 2026
