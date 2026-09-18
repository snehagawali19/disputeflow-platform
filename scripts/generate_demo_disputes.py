"""Generate 20 synthetic GitHub dispute files and hashed artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "disputes" / "input"
ARTIFACT_ROOT = ROOT / "disputes" / "artifacts"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((text.strip() + "\n").encode("utf-8"))


def artifact(dispute_id: str, filename: str, payload) -> dict:
    rel = f"disputes/artifacts/{dispute_id}/{filename}"
    dest = ROOT / rel
    if isinstance(payload, dict) or isinstance(payload, list):
        write_json(dest, payload)
    else:
        write_text(dest, str(payload))
    stem = Path(filename).stem
    return {
        "artifact_id": f"artifact-{dispute_id}-{stem}",
        "type": stem.replace("_confirmation", "").replace("_result", "").replace("_record", ""),
        "title": f"Synthetic {stem.replace('_', ' ')}",
        "repository_path": rel,
        "sha256": sha256_file(dest),
    }


def base(n: int, **overrides) -> dict:
    idx = f"{n:03d}"
    did = f"df-demo-{idx}"
    record = {
        "dispute_id": did,
        "processor": "stripe",
        "processor_transaction_id": f"ch_demo_{idx}",
        "payment_intent_id": f"pi_demo_{idx}",
        "order_id": f"order-demo-{idx}",
        "customer_id": f"cus_demo_{idx}",
        "merchant_name": f"Demo Merchant {idx}",
        "customer": {
            "name": f"Synthetic Customer {idx}",
            "email": f"customer{idx}@example.test",
        },
        "dispute_amount": 100.00 + n,
        "transaction_amount": 100.00 + n,
        "currency": "USD",
        "reason_code": "13.1",
        "reason_description": "Merchandise or services not received",
        "dispute_phase": "chargeback",
        "dispute_status": "needs_response",
        "dispute_received_at": "2026-09-01T10:00:00Z",
        "response_deadline": "2026-09-25T23:59:59Z",
        "authorization_at": "2026-08-20T12:00:00Z",
        "capture_at": "2026-08-20T12:01:10Z",
        "payment_method": {"type": "card", "brand": "visa", "last4": "4242", "funding": "credit"},
        "verification": {
            "avs_result": "Y",
            "cvv_result": "M",
            "three_d_secure": {
                "supported": True,
                "authenticated": True,
                "version": "2.2.0",
                "result": "authenticated",
            },
        },
        "network_context": {
            "ip_address": f"192.0.2.{n}",
            "device_id": f"device-demo-{idx}",
            "billing_shipping_match": True,
        },
        "order": {
            "product_description": f"Synthetic product {idx}",
            "quantity": 1,
            "order_created_at": "2026-08-20T11:55:00Z",
            "terms_version": "terms-2026-08",
            "refund_policy_version": "refund-policy-2026-08",
        },
        "shipping": {
            "carrier": "UPS",
            "tracking_number": f"1ZDEMO{idx}",
            "shipping_status": "delivered",
            "shipped_at": "2026-08-21T09:00:00Z",
            "delivered_at": "2026-08-24T15:30:00Z",
            "signature_required": True,
            "signature_obtained": True,
        },
        "customer_communications": [
            {
                "channel": "email",
                "occurred_at": "2026-08-25T09:00:00Z",
                "direction": "outbound",
                "summary": f"Synthetic support communication {idx}",
            }
        ],
        "refund_history": [],
        "usage_records": [],
        "artifacts": [],
        "expected_demo_outcome": "human_review",
    }
    record.update(overrides)
    return record


def build_cases() -> list[dict]:
    cases: list[dict] = []

    # 1 Fraud + successful 3DS
    c = base(1, reason_code="10.4", reason_description="Fraudulent transaction", expected_demo_outcome="contest")
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"order_id": c["order_id"], "amount": c["dispute_amount"]}),
        artifact(c["dispute_id"], "authentication_result.json", {"three_d_secure": "authenticated", "eci": "05"}),
        artifact(c["dispute_id"], "order_record.json", {"status": "captured"}),
    ]
    cases.append(c)

    # 2 Fraud + failed 3DS (missing positive auth evidence besides failed flag)
    c = base(
        2,
        reason_code="4837",
        reason_description="No cardholder authorization",
        dispute_amount=87.40,
        transaction_amount=87.40,
        expected_demo_outcome="human_review",
    )
    c["verification"]["three_d_secure"] = {
        "supported": True,
        "authenticated": False,
        "version": "2.1.0",
        "result": "failed",
    }
    c["verification"]["avs_result"] = "U"
    c["verification"]["cvv_result"] = "N"
    c["shipping"] = None
    c["customer_communications"] = []
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"order_id": c["order_id"], "amount": 87.40}),
        artifact(c["dispute_id"], "authentication_result.json", {"three_d_secure": "failed", "authenticated": False}),
    ]
    cases.append(c)

    # 3 Fraud + AVS mismatch with successful 3DS (conflict)
    c = base(3, reason_code="10.4", reason_description="Fraudulent transaction", expected_demo_outcome="human_review")
    c["verification"]["avs_result"] = "N"
    c["network_context"]["billing_shipping_match"] = False
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"order_id": c["order_id"]}),
        artifact(
            c["dispute_id"],
            "authentication_result.json",
            {"three_d_secure": "authenticated", "avs": "mismatch"},
        ),
        artifact(c["dispute_id"], "order_record.json", {"avs_conflict": True}),
    ]
    cases.append(c)

    # 4 PNR with confirmed delivery
    c = base(4, reason_code="13.1", dispute_amount=249.00, transaction_amount=249.00, expected_demo_outcome="contest")
    c["payment_method"] = {"type": "card", "brand": "mastercard", "last4": "4444", "funding": "debit"}
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"sku": "WIDGET-4"}),
        artifact(
            c["dispute_id"],
            "delivery_confirmation.json",
            {
                "status": "delivered",
                "tracking_number": "1ZDEMO004",
                "delivered_at": "2026-08-24T15:30:00Z",
            },
        ),
        artifact(c["dispute_id"], "signature_confirmation.json", {"signed_by": "Synthetic Recipient 004"}),
        artifact(c["dispute_id"], "tracking_events.json", [{"status": "delivered", "at": "2026-08-24T15:30:00Z"}]),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Tracking shared with customer"}),
    ]
    cases.append(c)

    # 5 PNR missing delivery evidence
    c = base(5, reason_code="4855", reason_description="Goods not received", expected_demo_outcome="human_review")
    c["shipping"] = {
        "carrier": "",
        "tracking_number": "",
        "shipping_status": "unknown",
        "shipped_at": None,
        "delivered_at": None,
        "signature_required": False,
        "signature_obtained": False,
    }
    c["customer_communications"] = []
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"note": "No carrier confirmation attached"}),
        artifact(c["dispute_id"], "order_record.json", {"fulfillment": "unconfirmed"}),
    ]
    cases.append(c)

    # 6 Digital product with login/download
    c = base(
        6,
        reason_code="13.1",
        reason_description="Digital goods not received",
        dispute_amount=19.99,
        transaction_amount=19.99,
        expected_demo_outcome="contest",
    )
    c["shipping"] = None
    c["order"]["product_description"] = "Synthetic digital download license"
    c["usage_records"] = [
        {"event": "login", "occurred_at": "2026-08-20T13:00:00Z", "device_id": "device-demo-006"},
        {"event": "download", "occurred_at": "2026-08-20T13:04:00Z", "asset": "license-006.bin"},
    ]
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"product": "digital-license"}),
        artifact(
            c["dispute_id"],
            "usage_activity.json",
            {"logins": 3, "downloads": 1, "last_login": "2026-08-22T08:00:00Z"},
        ),
        artifact(c["dispute_id"], "terms_acceptance.json", {"accepted_at": "2026-08-20T11:55:00Z"}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Download link resent"}),
    ]
    cases.append(c)

    # 7 Product unacceptable
    c = base(7, reason_code="13.3", reason_description="Merchandise not as described", expected_demo_outcome="human_review")
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"sku": "GARMENT-7"}),
        artifact(c["dispute_id"], "refund_policy.json", {"window_days": 30, "condition": "unused"}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Customer reported color mismatch"}),
        artifact(c["dispute_id"], "shipping_confirmation.json", {"shipped": True, "tracking_number": "1ZDEMO007"}),
    ]
    cases.append(c)

    # 8 Duplicate
    c = base(8, reason_code="duplicate", reason_description="Duplicate processing", expected_demo_outcome="contest")
    c["reason_code"] = "12.6.1"
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"charge": "ch_demo_008"}),
        artifact(c["dispute_id"], "receipt.json", {"related_charge": "ch_demo_008b", "amount": 108.0}),
        artifact(c["dispute_id"], "order_record.json", {"duplicate_candidate": "order-demo-008b"}),
    ]
    cases.append(c)

    # 9 Credit not processed after refund request (conflict: request, no refund txn)
    c = base(
        9,
        reason_code="credit_not_processed",
        reason_description="Credit not processed",
        expected_demo_outcome="human_review",
    )
    c["refund_history"] = []
    c["customer_communications"] = [
        {
            "channel": "email",
            "occurred_at": "2026-08-22T16:00:00Z",
            "direction": "inbound",
            "summary": "Synthetic customer requested refund; no refund transaction is on file",
        }
    ]
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"amount": 109.0}),
        artifact(c["dispute_id"], "customer_communication.json", {"refund_requested": True, "refund_issued": False}),
        artifact(c["dispute_id"], "refund_policy.json", {"refunds": "merchant review required"}),
    ]
    cases.append(c)

    # 10 Subscription canceled before renewal
    c = base(
        10,
        reason_code="subscription_canceled",
        reason_description="Canceled recurring transaction",
        dispute_amount=14.00,
        transaction_amount=14.00,
        expected_demo_outcome="contest",
    )
    c["shipping"] = None
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"plan": "monthly-demo"}),
        artifact(
            c["dispute_id"],
            "terms_acceptance.json",
            {"cancel_by": "2026-08-15", "renewal_at": "2026-08-20T12:00:00Z", "canceled_at": "2026-08-12T09:00:00Z"},
        ),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Cancellation confirmed before renewal"}),
        artifact(c["dispute_id"], "order_record.json", {"subscription_status": "canceled"}),
    ]
    cases.append(c)

    # 11 Subscription used after cancellation (conflict)
    c = base(11, reason_code="4841", reason_description="Canceled recurring transaction", expected_demo_outcome="human_review")
    c["shipping"] = None
    c["usage_records"] = [
        {"event": "stream", "occurred_at": "2026-08-21T18:00:00Z", "minutes": 42},
    ]
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"renewal": True}),
        artifact(
            c["dispute_id"],
            "terms_acceptance.json",
            {"canceled_at": "2026-08-10T09:00:00Z", "renewal_at": "2026-08-20T12:00:00Z"},
        ),
        artifact(c["dispute_id"], "usage_activity.json", {"used_after_cancel": True}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Customer canceled then continued access"}),
    ]
    cases.append(c)

    # 12 Processing error — incomplete evidence
    c = base(12, reason_code="4860", reason_description="Processing error", expected_demo_outcome="human_review")
    c["shipping"] = None
    c["customer_communications"] = []
    c["verification"]["three_d_secure"]["authenticated"] = False
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"note": "Processor reported a settlement mismatch; no correction file supplied"}),
    ]
    cases.append(c)

    # 13 Partial refund already issued
    c = base(13, reason_code="13.6", reason_description="Credit not processed", expected_demo_outcome="contest")
    c["refund_history"] = [
        {"refund_id": "re_demo_013", "amount": 40.00, "currency": "USD", "issued_at": "2026-08-26T12:00:00Z"}
    ]
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"original": 113.0}),
        artifact(c["dispute_id"], "refund_confirmation.json", {"refund_id": "re_demo_013", "amount": 40.00}),
        artifact(c["dispute_id"], "refund_policy.json", {"partial_refunds": True}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Partial refund of 40.00 issued"}),
    ]
    cases.append(c)

    # 14 Order canceled before capture — inquiry
    c = base(
        14,
        reason_code="13.2",
        reason_description="Cancelled merchandise",
        dispute_phase="inquiry",
        dispute_status="warning_needs_response",
        capture_at=None,
        expected_demo_outcome="accept",
    )
    c["shipping"] = None
    c["artifacts"] = [
        artifact(c["dispute_id"], "order_record.json", {"status": "canceled_before_capture"}),
        artifact(c["dispute_id"], "invoice.json", {"authorized_only": True}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Order canceled prior to capture"}),
        artifact(c["dispute_id"], "terms_acceptance.json", {"cancel_before_ship": True}),
    ]
    cases.append(c)

    # 15 Billing/shipping mismatch
    c = base(15, reason_code="13.1", expected_demo_outcome="human_review")
    c["network_context"]["billing_shipping_match"] = False
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"ship_to": "synthetic-shipping-B"}),
        artifact(c["dispute_id"], "shipping_confirmation.json", {"tracking_number": "1ZDEMO015", "status": "in_transit"}),
        artifact(c["dispute_id"], "order_record.json", {"billing_shipping_match": False}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Address mismatch under review"}),
    ]
    c["shipping"]["shipping_status"] = "in_transit"
    c["shipping"]["delivered_at"] = None
    c["shipping"]["signature_obtained"] = False
    cases.append(c)

    # 16 Strong evidence / high estimated win
    c = base(
        16,
        reason_code="13.1",
        dispute_amount=32.50,
        transaction_amount=32.50,
        expected_demo_outcome="contest",
    )
    c["payment_method"]["last4"] = "0002"
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"sku": "CABLE-16"}),
        artifact(
            c["dispute_id"],
            "delivery_confirmation.json",
            {"status": "delivered", "tracking_number": "1ZDEMO016", "delivered_at": "2026-08-23T11:00:00Z"},
        ),
        artifact(c["dispute_id"], "signature_confirmation.json", {"signed_by": "Synthetic Recipient 016"}),
        artifact(c["dispute_id"], "tracking_events.json", [{"status": "delivered"}]),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Delivery photos shared"}),
        artifact(c["dispute_id"], "terms_acceptance.json", {"accepted": True}),
        artifact(c["dispute_id"], "authentication_result.json", {"authenticated": True}),
    ]
    cases.append(c)

    # 17 Weak evidence / low win — fraudulent with no auth
    c = base(17, reason_code="4837", reason_description="No cardholder authorization", expected_demo_outcome="human_review")
    c["verification"] = {
        "avs_result": "N",
        "cvv_result": "N",
        "three_d_secure": {"supported": False, "authenticated": False, "version": "", "result": "unavailable"},
    }
    c["shipping"] = None
    c["customer_communications"] = []
    c["network_context"]["ip_address"] = ""
    c["network_context"]["device_id"] = ""
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"note": "Checkout metadata incomplete"}),
    ]
    cases.append(c)

    # 18 Pre-arbitration
    c = base(
        18,
        reason_code="4853",
        reason_description="Cardholder dispute — not as described",
        dispute_phase="pre_arbitration",
        dispute_amount=410.00,
        transaction_amount=410.00,
        expected_demo_outcome="human_review",
    )
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"sku": "DEVICE-18"}),
        artifact(c["dispute_id"], "refund_policy.json", {"restocking_fee": "15%"}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Issuer escalated to pre-arbitration"}),
        artifact(c["dispute_id"], "order_record.json", {"prior_chargeback_ref": "cb-demo-018"}),
    ]
    cases.append(c)

    # 19 Arbitration
    c = base(
        19,
        reason_code="13.3",
        dispute_phase="arbitration",
        dispute_amount=890.00,
        transaction_amount=890.00,
        expected_demo_outcome="human_review",
    )
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"sku": "APPLIANCE-19"}),
        artifact(c["dispute_id"], "delivery_confirmation.json", {"status": "delivered", "tracking_number": "1ZDEMO019"}),
        artifact(c["dispute_id"], "customer_communication.json", {"summary": "Case in arbitration; prior packet attached"}),
        artifact(c["dispute_id"], "order_record.json", {"phase": "arbitration"}),
        artifact(c["dispute_id"], "terms_acceptance.json", {"accepted": True}),
    ]
    cases.append(c)

    # 20 Contradictory incomplete — delivered but no tracking
    c = base(20, reason_code="13.1", expected_demo_outcome="human_review")
    c["shipping"] = {
        "carrier": "USPS",
        "tracking_number": "",
        "shipping_status": "delivered",
        "shipped_at": "2026-08-21T09:00:00Z",
        "delivered_at": "2026-08-24T15:30:00Z",
        "signature_required": True,
        "signature_obtained": False,
    }
    c["artifacts"] = [
        artifact(c["dispute_id"], "invoice.json", {"note": "Internal status says delivered; tracking number was never stored"}),
        artifact(c["dispute_id"], "order_record.json", {"contradiction": "delivered_without_tracking"}),
    ]
    cases.append(c)

    return cases


def main() -> None:
    if INPUT_DIR.exists():
        for path in INPUT_DIR.glob("dispute-*.json"):
            path.unlink()
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = build_cases()
    if len(cases) != 20:
        raise SystemExit(f"Expected 20 cases, got {len(cases)}")
    for index, case in enumerate(cases, start=1):
        dest = INPUT_DIR / f"dispute-{index:03d}.json"
        write_json(dest, case)
        print(f"wrote {dest.relative_to(ROOT)}")
    print("generated 20 synthetic disputes")


if __name__ == "__main__":
    main()
