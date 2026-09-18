"""Deterministic evidence-quality checks used by ML, routing, and filing."""

from __future__ import annotations

import re
from collections.abc import Iterable

from backend.models.dispute import DisputeCase, EvidenceItem


_TRACKING_VALUE = re.compile(
    r"(?:tracking(?:\s+(?:number|id))?|awb|waybill)\s*[:#-]?\s*[A-Z0-9][A-Z0-9-]{5,}",
    re.IGNORECASE,
)
_DATE_VALUE = re.compile(r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]20\d{2})\b")


def credible_delivery_proof(item: EvidenceItem) -> bool:
    if item.evidence_type not in {"delivery_proof", "signed_delivery"}:
        return False
    text = f"{item.title} {item.content}".lower()
    delivered = any(term in text for term in ("delivered", "delivery confirmed", "proof of delivery"))
    corroboration = (
        bool(_TRACKING_VALUE.search(item.content))
        or any(term in text for term in ("signed by", "recipient signature", "signature captured"))
        or ("carrier" in text and bool(_DATE_VALUE.search(item.content)))
    )
    return delivered and corroboration


def positive_authentication(case: DisputeCase, items: Iterable[EvidenceItem]) -> bool:
    txn = case.transaction
    if txn and (txn.three_ds_authenticated is True or txn.avs_match is True or txn.cvv_verified is True):
        return True
    positive_terms = ("authenticated=true", "verified=true", "match=true", "successful authentication")
    return any(
        item.evidence_type in {"3ds_authentication", "avs_cvv_verification", "ip_address_log"}
        and any(term in item.content.lower().replace(" ", "") for term in positive_terms[:3])
        for item in items
    )


def deterministic_evidence_gaps(case: DisputeCase, items: list[EvidenceItem]) -> list[str]:
    reason = case.reason_code.value
    types = {item.evidence_type for item in items}
    gaps: list[str] = []
    if reason == "product_not_received":
        if not any(credible_delivery_proof(item) for item in items):
            gaps.append("Verified delivery confirmation with tracking/signature details")
        if "customer_communication" not in types:
            gaps.append("Customer delivery communication")
    elif reason in {"fraudulent", "unauthorized"} and not positive_authentication(case, items):
        gaps.append("Positive authentication, AVS/CVV, or IP/device evidence")
    elif reason == "product_unacceptable" and not types.intersection({"customer_communication", "refund_policy"}):
        gaps.append("Customer communication or applicable refund policy")
    elif reason == "duplicate" and sum(item.evidence_type == "transaction_receipt" for item in items) < 2:
        gaps.append("Two distinct transaction records demonstrating separate purchases")
    elif reason == "subscription_canceled" and not types.intersection({"terms_of_service", "customer_communication"}):
        gaps.append("Cancellation terms and customer cancellation history")
    return gaps

