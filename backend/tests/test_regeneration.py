from datetime import datetime
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.main import generate_data
from app.models import AuditAction, InternalTransaction, ReconciliationRun
from app.schemas import GenerateDataRequest
from data_generator import generate_reconciliation_datasets


class RegenerationTests(unittest.TestCase):
    def test_volumes_below_the_dashboard_minimum_are_rejected(self):
        with self.assertRaises(ValueError):
            GenerateDataRequest(num_transactions=299)
        with self.assertRaises(ValueError):
            generate_reconciliation_datasets(num_transactions=299)

    def test_rounding_sample_fits_available_payments(self):
        for volume, expected in [(300, 240), (400, 340), (1200, 350)]:
            with self.subTest(volume=volume):
                dataset = generate_reconciliation_datasets(num_transactions=volume)
                rounding = dataset["metadata"]["planted_gaps"]["rounding_differences"]
                self.assertEqual(rounding["count"], expected)
                self.assertEqual(len(set(rounding["affected_txn_ids"])), expected)

    def test_failed_reconciliation_preserves_previous_dataset_and_audit(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        payment = dict(timestamp=datetime(2026, 10, 1), amount=10, fee_expected=1, net_expected=9)
        with Session(engine) as db:
            db.add(InternalTransaction(transaction_id="old", **payment))
            db.add(ReconciliationRun(run_id="old-run", period_start=datetime(2026, 10, 1), period_end=datetime(2026, 10, 31)))
            db.add(AuditAction(run_id="old-run", action_type="NOTE_ADDED", notes="Keep this note"))
            db.commit()

        replacement = {"internal_transactions": [dict(transaction_id="new", **payment)], "bank_settlements": []}
        with patch("app.main.generate_reconciliation_datasets", return_value=replacement):
            with patch("app.main.ReconciliationEngine.run_reconciliation", side_effect=RuntimeError("run failed")):
                with self.assertRaisesRegex(RuntimeError, "run failed"), Session(engine) as db:
                    generate_data(GenerateDataRequest(), db)

            with Session(engine) as db:
                self.assertEqual(db.query(InternalTransaction).one().transaction_id, "old")
                self.assertEqual(db.query(ReconciliationRun).one().run_id, "old-run")
                self.assertEqual(db.query(AuditAction).one().notes, "Keep this note")
                result = generate_data(GenerateDataRequest(), db)

        with Session(engine) as db:
            self.assertEqual(db.query(InternalTransaction).one().transaction_id, "new")
            self.assertEqual(db.query(AuditAction).count(), 0)
            self.assertEqual(db.query(ReconciliationRun).one().run_id, result["reconciliation"]["run_id"])
