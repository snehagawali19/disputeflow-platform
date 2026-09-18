"""Generate synthetic dispute training data for the XGBoost model."""

from __future__ import annotations

import os
import random
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

random.seed(42)
np.random.seed(42)

N = 2000
reason_codes = [
    "fraudulent",
    "unauthorized",
    "product_not_received",
    "product_unacceptable",
    "duplicate",
    "subscription_canceled",
]
risk_levels = [0, 1, 2, 3]

records = []
for _ in range(N):
    reason = random.choice(reason_codes)
    amount = round(random.lognormvariate(9, 1.5), 2)
    evidence_count = random.randint(0, 8)
    avg_relevance = round(random.uniform(0.1, 1.0), 2) if evidence_count > 0 else 0
    max_relevance = round(min(avg_relevance + random.uniform(0, 0.3), 1.0), 2) if evidence_count > 0 else 0
    has_delivery = random.choice([0, 1])
    has_auth = random.choice([0, 1])
    has_comms = random.choice([0, 1])
    has_receipt = random.choice([0, 1])
    avs = random.choice([0, 1])
    cvv = random.choice([0, 1])
    three_ds = random.choice([0, 1])
    risk = random.choice(risk_levels)
    card = random.choice([0, 1])
    upi = 1 - card

    win_score = 0.25
    if evidence_count >= 3:
        win_score += 0.15
    if avg_relevance > 0.6:
        win_score += 0.15
    if has_delivery and reason == "product_not_received":
        win_score += 0.25
    if has_auth and reason in {"fraudulent", "unauthorized"}:
        win_score += 0.2
    if three_ds:
        win_score += 0.1
    if has_comms:
        win_score += 0.05
    if amount > 100000:
        win_score -= 0.1
    if risk >= 2:
        win_score -= 0.05
    if reason == "fraudulent" and not has_auth:
        win_score -= 0.15
    win_score = max(0.05, min(0.95, win_score))
    outcome = 1 if random.random() < win_score else 0

    records.append(
        {
            "dispute_amount": amount,
            "evidence_count": evidence_count,
            "avg_evidence_relevance": avg_relevance,
            "max_evidence_relevance": max_relevance,
            "has_delivery_proof": has_delivery,
            "has_auth_proof": has_auth,
            "has_customer_communication": has_comms,
            "has_transaction_receipt": has_receipt,
            "reason_code_fraudulent": int(reason == "fraudulent"),
            "reason_code_product_not_received": int(reason == "product_not_received"),
            "reason_code_product_unacceptable": int(reason == "product_unacceptable"),
            "reason_code_unauthorized": int(reason == "unauthorized"),
            "reason_code_duplicate": int(reason == "duplicate"),
            "reason_code_subscription": int(reason == "subscription_canceled"),
            "payment_method_card": card,
            "payment_method_upi": upi,
            "avs_match": avs,
            "cvv_verified": cvv,
            "three_ds": three_ds,
            "risk_level_numeric": risk,
            "outcome_won": outcome,
        }
    )

df = pd.DataFrame(records)
output_path = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "backend",
    "data",
    "synthetic",
    "training_disputes.csv",
)
os.makedirs(os.path.dirname(output_path), exist_ok=True)
df.to_csv(output_path, index=False)
print(f"Generated {N} synthetic disputes -> {output_path}")
print(f"Win rate: {df['outcome_won'].mean():.1%}")
