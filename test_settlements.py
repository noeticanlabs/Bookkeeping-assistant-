from decimal import Decimal

import pytest

from app import BankDeposit, Bookkeeper, Payment
from settlements import SettlementEvidence, SettlementStore
from verifier_registry import FAIL, PASS, UNKNOWN, default_registry


def _verification(results, settlement_id):
    return next(
        r for r in results
        if r.verifier_id == "V-SETTLEMENT-RECONCILIATION" and r.object_id == settlement_id
    )


def test_aggregate_settlement_reconciles_multiple_payments_and_explicit_deductions(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_payment(Payment("PAY-2", Decimal("500")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("1305")))

    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-1", "Stripe", "po_123")
    store.add_payment("SET-1", "PAY-1", book)
    store.add_payment("SET-1", "PAY-2", book)
    store.add_adjustment("FEE-1", "SET-1", "fee", Decimal("45"))
    store.add_adjustment("REFUND-1", "SET-1", "refund", Decimal("100"))
    store.add_adjustment("CB-1", "SET-1", "chargeback", Decimal("50"))
    store.link_deposit("SET-1", "DEP-1", book)

    recon = store.reconcile("SET-1", book)

    assert recon.gross_payments == Decimal("1500")
    assert recon.fees == Decimal("45")
    assert recon.refunds == Decimal("100")
    assert recon.chargebacks == Decimal("50")
    assert recon.expected_net == Decimal("1305")
    assert recon.actual_deposit == Decimal("1305")
    assert recon.difference == Decimal("0")
    assert recon.status == "reconciled"

    verified = _verification(default_registry(store).run(book), "SET-1")
    assert verified.status == PASS
    assert verified.evidence["calculated_net"] == "1305"
    assert verified.evidence["bank_deposit"] == "1305"


def test_settlement_difference_is_a_deterministic_failure(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("1000")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("960")))

    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-1", "Stripe")
    store.add_payment("SET-1", "PAY-1", book)
    store.add_adjustment("FEE-1", "SET-1", "fee", Decimal("30"))
    store.link_deposit("SET-1", "DEP-1", book)

    recon = store.reconcile("SET-1", book)
    assert recon.expected_net == Decimal("970")
    assert recon.actual_deposit == Decimal("960")
    assert recon.difference == Decimal("-10")
    assert recon.status == "difference"

    verified = _verification(default_registry(store).run(book), "SET-1")
    assert verified.status == FAIL
    assert verified.review_required is True
    assert verified.evidence["difference"] == "-10"


def test_open_or_incomplete_settlement_is_unknown_not_false_failure(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))

    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-OPEN", "Stripe")
    store.add_payment("SET-OPEN", "PAY-1", book)

    result = _verification(default_registry(store).run(book), "SET-OPEN")
    assert result.status == UNKNOWN
    assert result.evidence["deposit_id"] == "none"


def test_imported_payout_without_allocated_payments_stays_open_not_failed(tmp_path):
    book = Bookkeeper()
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    imported = store.import_evidence([
        SettlementEvidence("STRIPE-SET:po_1", "Stripe", Decimal("97"), "po_1")
    ])

    assert imported.added == 1
    recon = store.reconcile("STRIPE-SET:po_1", book)
    assert recon.reported_net == Decimal("97")
    assert recon.status == "open"
    assert _verification(default_registry(store).run(book), "STRIPE-SET:po_1").status == UNKNOWN


def test_component_difference_is_separate_from_bank_difference(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("97")))
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.import_evidence([
        SettlementEvidence("SET-1", "Stripe", Decimal("97"), "po_1")
    ])
    store.add_payment("SET-1", "PAY-1", book)
    store.add_adjustment("FEE-1", "SET-1", "fee", Decimal("4"))
    store.link_deposit("SET-1", "DEP-1", book)

    recon = store.reconcile("SET-1", book)
    assert recon.expected_net == Decimal("96")
    assert recon.reported_net == Decimal("97")
    assert recon.component_difference == Decimal("1")
    assert recon.bank_difference == Decimal("0")
    assert recon.status == "difference"

    verified = _verification(default_registry(store).run(book), "SET-1")
    assert verified.status == FAIL
    assert "do not explain" in verified.summary
    assert verified.evidence["components_to_payout_difference"] == "1"
    assert verified.evidence["payout_to_bank_difference"] == "0"


def test_bank_difference_is_separate_from_processor_component_difference(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("96")))
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.import_evidence([
        SettlementEvidence("SET-1", "Stripe", Decimal("97"), "po_1")
    ])
    store.add_payment("SET-1", "PAY-1", book)
    store.add_adjustment("FEE-1", "SET-1", "fee", Decimal("3"))
    store.link_deposit("SET-1", "DEP-1", book)

    recon = store.reconcile("SET-1", book)
    assert recon.expected_net == Decimal("97")
    assert recon.reported_net == Decimal("97")
    assert recon.component_difference == Decimal("0")
    assert recon.bank_difference == Decimal("-1")
    assert recon.status == "difference"

    verified = _verification(default_registry(store).run(book), "SET-1")
    assert verified.status == FAIL
    assert "bank deposit" in verified.summary
    assert verified.evidence["components_to_payout_difference"] == "0"
    assert verified.evidence["payout_to_bank_difference"] == "-1"


def test_payment_cannot_be_counted_in_two_settlements(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-1", "Stripe")
    store.create("SET-2", "Stripe")
    store.add_payment("SET-1", "PAY-1", book)

    with pytest.raises(ValueError, match="already assigned"):
        store.add_payment("SET-2", "PAY-1", book)


def test_aggregate_settlement_rejects_deposit_already_directly_matched_to_payment(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("100")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("97"), "PAY-1", processor_fee=Decimal("3")))

    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-1", "Stripe")
    store.add_payment("SET-1", "PAY-1", book)

    with pytest.raises(ValueError, match="directly matched"):
        store.link_deposit("SET-1", "DEP-1", book)


def test_deposit_cannot_be_used_by_two_aggregate_settlements(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("PAY-1", Decimal("50")))
    book.add_payment(Payment("PAY-2", Decimal("50")))
    book.add_deposit(BankDeposit("DEP-1", Decimal("50")))

    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.create("SET-1", "Stripe")
    store.create("SET-2", "Stripe")
    store.add_payment("SET-1", "PAY-1", book)
    store.add_payment("SET-2", "PAY-2", book)
    store.link_deposit("SET-1", "DEP-1", book)

    with pytest.raises(ValueError, match="another settlement"):
        store.link_deposit("SET-2", "DEP-1", book)
