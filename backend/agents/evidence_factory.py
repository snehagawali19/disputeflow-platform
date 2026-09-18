"""Build evidence only from supplied transaction and case facts."""

from __future__ import annotations

from backend.models.dispute import DisputeCase, EvidenceItem


def evidence_from_supplied_facts(case: DisputeCase) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    txn = case.transaction
    if txn is None:
        return items

    items.append(
        EvidenceItem(
            evidence_type="transaction_receipt",
            title="Processor transaction record",
            content=(
                f"Transaction {txn.transaction_id} for {txn.amount} {txn.currency} "
                f"via {txn.payment_method} on {txn.timestamp.isoformat()} "
                f"for merchant {txn.merchant_name or 'unknown'}."
            ),
            source="supplied_transaction",
            relevance_score=0.55,
        )
    )
    if txn.avs_match is not None:
        items.append(
            EvidenceItem(
                evidence_type="avs_cvv_verification",
                title="AVS result from processor",
                content=f"Address verification match={txn.avs_match}.",
                source="supplied_transaction",
                relevance_score=0.7 if txn.avs_match else 0.25,
            )
        )
    if txn.cvv_verified is not None:
        items.append(
            EvidenceItem(
                evidence_type="avs_cvv_verification",
                title="CVV verification result",
                content=f"CVV verified={txn.cvv_verified}.",
                source="supplied_transaction",
                relevance_score=0.65 if txn.cvv_verified else 0.2,
            )
        )
    if txn.three_ds_authenticated is not None:
        items.append(
            EvidenceItem(
                evidence_type="3ds_authentication",
                title="3-D Secure authentication flag",
                content=f"3DS authenticated={txn.three_ds_authenticated}.",
                source="supplied_transaction",
                relevance_score=0.8 if txn.three_ds_authenticated else 0.2,
            )
        )
    if txn.ip_address:
        items.append(
            EvidenceItem(
                evidence_type="ip_address_log",
                title="Captured checkout IP",
                content=f"Checkout IP recorded as {txn.ip_address}.",
                source="supplied_transaction",
                relevance_score=0.45,
            )
        )
    if txn.device_fingerprint:
        items.append(
            EvidenceItem(
                evidence_type="device_fingerprint",
                title="Device fingerprint",
                content=f"Device fingerprint {txn.device_fingerprint} captured at checkout.",
                source="supplied_transaction",
                relevance_score=0.45,
            )
        )
    if txn.billing_address:
        items.append(
            EvidenceItem(
                evidence_type="other",
                title="Billing address on file",
                content=f"Billing address supplied: {txn.billing_address}.",
                source="supplied_transaction",
                relevance_score=0.35,
            )
        )
    if txn.shipping_address:
        items.append(
            EvidenceItem(
                evidence_type="other",
                title="Shipping address on file",
                content=f"Shipping address supplied: {txn.shipping_address}. Not delivery confirmation.",
                source="supplied_transaction",
                relevance_score=0.25,
            )
        )
    return items


def evidence_from_artifacts(artifacts: list[dict]) -> list[EvidenceItem]:
    """Attach only caller-supplied typed artifacts. Never invent content."""
    allowed = {
        "delivery_proof",
        "customer_communication",
        "refund_policy",
        "transaction_receipt",
        "service_documentation",
        "ip_address_log",
        "device_fingerprint",
        "avs_cvv_verification",
        "3ds_authentication",
        "signed_delivery",
        "usage_log",
        "terms_of_service",
        "other",
    }
    items: list[EvidenceItem] = []
    for raw in artifacts:
        evidence_type = str(raw.get("evidence_type") or "other")
        if evidence_type not in allowed:
            evidence_type = "other"
        title = str(raw.get("title") or "").strip()
        content = str(raw.get("content") or "").strip()
        if not title or not content:
            continue
        items.append(
            EvidenceItem(
                evidence_type=evidence_type,  # type: ignore[arg-type]
                title=title[:200],
                content=content[:4000],
                source="supplied_artifact",
                relevance_score=0.7 if evidence_type in {"delivery_proof", "signed_delivery"} else 0.55,
            )
        )
    return items
