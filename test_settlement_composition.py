from decimal import Decimal

from app import BankDeposit, Bookkeeper, Payment
from settlements import SettlementComponentEvidence, SettlementEvidence, SettlementStore


def component(component_id="txn_1", *, kind="payment", amount="100", fee="3", net="97",
              category="charge", tx_type="charge", payment_id="STRIPE-PAY:pi_1"):
    return SettlementComponentEvidence(
        component_id=component_id,
        kind=kind,
        amount=Decimal(amount),
        fee=Decimal(fee),
        net=Decimal(net),
        currency="usd",
        reporting_category=category,
        transaction_type=tx_type,
        source_id="ch_1",
        payment_id=payment_id,
        description="Stripe component",
    )


def test_reconstructed_processor_batch_maps_payment_and_reconciles_to_bank(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("STRIPE-PAY:pi_1", Decimal("100")))
    book.add_deposit(BankDeposit("BANK-DEP-1", Decimal("97")))
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")

    result = store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_1", "Stripe", Decimal("97"), "po_1",
            components=(component(),),
            composition_complete=True,
            composition_note="automatic payout reconstructed",
        )
    ], book=book)

    assert result.added == 1
    assert store.payment_ids("STRIPE-SET:po_1") == ["STRIPE-PAY:pi_1"]
    store.link_deposit("STRIPE-SET:po_1", "BANK-DEP-1", book)
    recon = store.reconcile("STRIPE-SET:po_1", book)

    assert recon.processor_component_count == 1
    assert recon.processor_component_net == Decimal("97")
    assert recon.expected_net == Decimal("97")
    assert recon.component_difference == Decimal("0")
    assert recon.bank_difference == Decimal("0")
    assert recon.unmapped_payment_component_ids == ()
    assert recon.unclassified_component_ids == ()
    assert recon.status == "reconciled"


def test_reconstructed_batch_detects_processor_payout_difference(tmp_path):
    book = Bookkeeper()
    book.add_payment(Payment("STRIPE-PAY:pi_1", Decimal("100")))
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_1", "Stripe", Decimal("98"), "po_1",
            components=(component(),), composition_complete=True,
            composition_note="automatic payout reconstructed",
        )
    ], book=book)

    recon = store.reconcile("STRIPE-SET:po_1", book)
    assert recon.processor_component_net == Decimal("97")
    assert recon.component_difference == Decimal("1")
    assert recon.status == "difference"


def test_processor_component_amount_fee_net_identity_is_enforced(tmp_path):
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    bad = component(fee="4", net="97")
    result = store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_bad", "Stripe", Decimal("97"), "po_bad",
            components=(bad,), composition_complete=True,
        )
    ])

    assert result.added == 0
    assert len(result.errors) == 1
    assert "amount - fee = net" in result.errors[0]
    try:
        store.get("STRIPE-SET:po_bad")
        assert False, "invalid component evidence must not create settlement state"
    except ValueError:
        pass


def test_immutable_component_conflict_is_rejected_on_resync(tmp_path):
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    first = SettlementEvidence(
        "STRIPE-SET:po_1", "Stripe", Decimal("97"), "po_1",
        components=(component(),), composition_complete=True,
    )
    assert store.import_evidence([first]).added == 1

    changed = component(amount="101", fee="4", net="97")
    result = store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_1", "Stripe", Decimal("97"), "po_1",
            components=(changed,), composition_complete=True,
        )
    ])

    assert result.added == 0
    assert len(result.errors) == 1
    assert "immutable evidence" in result.errors[0]
    persisted = store.components("STRIPE-SET:po_1")[0]
    assert persisted.amount == Decimal("100")
    assert persisted.fee == Decimal("3")


def test_unmapped_customer_payment_keeps_full_bookkeeping_reconciliation_unknown(tmp_path):
    book = Bookkeeper()
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_1", "Stripe", Decimal("97"), "po_1",
            components=(component(),), composition_complete=True,
            composition_note="automatic payout reconstructed",
        )
    ], book=book)

    recon = store.reconcile("STRIPE-SET:po_1", book)
    assert recon.component_difference == Decimal("0")
    assert recon.unmapped_payment_component_ids == ("txn_1",)
    assert recon.status == "unknown"


def test_explicit_other_processor_movement_stays_visible_and_requires_review(tmp_path):
    book = Bookkeeper()
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    other = component(
        component_id="txn_reserve", kind="other", amount="10", fee="0", net="10",
        category="reserve_release", tx_type="reserve_release", payment_id=None,
    )
    store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_reserve", "Stripe", Decimal("10"), "po_reserve",
            components=(other,), composition_complete=True,
            composition_note="automatic payout reconstructed",
        )
    ], book=book)

    recon = store.reconcile("STRIPE-SET:po_reserve", book)
    assert recon.processor_component_net == Decimal("10")
    assert recon.category_totals == (("reserve_release", Decimal("10")),)
    assert recon.unclassified_component_ids == ("txn_reserve",)
    assert recon.status == "unknown"


def test_manual_or_instant_processor_payout_composition_is_unknown_not_guessed(tmp_path):
    book = Bookkeeper()
    store = SettlementStore(tmp_path / "bookkeeper.sqlite3")
    store.import_evidence([
        SettlementEvidence(
            "STRIPE-SET:po_manual", "Stripe", Decimal("50"), "po_manual",
            composition_complete=False,
            composition_note="Stripe does not expose deterministic composition for this payout",
        )
    ])

    recon = store.reconcile("STRIPE-SET:po_manual", book)
    assert recon.processor_component_count == 0
    assert recon.composition_complete is False
    assert recon.status == "unknown"
