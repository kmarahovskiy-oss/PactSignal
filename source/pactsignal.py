"""PactSignal: evidence-first local agreement-to-payment analysis.

The model suggests message intent.  Exact source excerpts and conservative
guards remain visible.  The program never authenticates a sender, contract or
payment and never sends, accepts, invoices or moves money.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from sklearn.pipeline import FeatureUnion
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from training_data import LABELS, STRESS_ROWS, ADVERSARIAL_ROWS, training_rows
from mailbox_ingest import MailboxStore, sync_source
from settlement_ingest import read_settlements_csv
from workflow_signals import signals as workflow_signals, annotate_threads

MAX_MESSAGES = 500
MAX_TEXT = 20_000
TRUSTED_CONFIRMATIONS = {"user_confirmed_received", "provider_notice_authenticated"}
PAYMENT_PROVIDER_MARKERS = ("paypal", "stripe", "wise", "payoneer", "revolut", "bank statement")
NAME_STOPWORDS = {
    "from", "mail", "payment", "payments", "service", "support", "team", "billing",
    "example", "test", "gmail", "outlook", "proton", "hello", "info", "admin",
}

AMOUNT_PATTERNS = (
    re.compile(r"(?<!\w)(USD|EUR|GBP|UAH|USDC)\s*([0-9][0-9 ,.]*[0-9]|[0-9])\b", re.I),
    re.compile(r"(?<!\w)([$€£₴])\s*([0-9][0-9 ,.]*[0-9]|[0-9])"),
    re.compile(r"(?<!\w)([0-9][0-9 ,.]*[0-9]|[0-9])\s*(USD|EUR|GBP|UAH|USDC)\b", re.I),
)
SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₴": "UAH"}

GUARDS = [
    ("admin", re.compile(r"\b(?:can you explain|what .{0,40} means|documentation links?|support ticket|case .{0,20} routed|portal .{0,45} maintenance|confirm which timezone|received your question|eligible for (?:rewards?|participation)|free sandbox accounts?|production subscription)\b", re.I)),
    ("rejection", re.compile(r"\b(?:declined|rejected|not selected|did not win|chose another|will not proceed|not move forward|approval was denied)\b", re.I)),
    # A failed/reversed payment is the highest financial-risk fact in a mixed
    # message (for example: "work accepted, but the payout failed").
    ("payment_failure", re.compile(r"\b(?:not received|no funds|never arrived|failed|returned unpaid|reversed|cancelled the payout|payout was rejected)\b", re.I)),
    ("acceptance", re.compile(r"\b(?:accepted (?:the|your) (?:final )?(?:work|correction|report|submission|result)|work (?:was|is) accepted|posted (?:the|your) (?:notes|correction)|reproduced the bug and fixed it|merged (?:the|your) (?:fix|change)|approved the delivered work)\b", re.I)),
    ("offer", re.compile(r"\b(?:honorarium|budget|ceiling|first accepted|winner|prize|reward|can offer|could you quote|would .{0,25} cover)\b", re.I)),
    ("delivery", re.compile(r"\b(?:delivered|submitted|attached|handed over|submission is complete|final files?|final build|final report)\b", re.I)),
    ("payment_notice", re.compile(r"\b(?:payment completed|settlement complete|credited|credit advice|transaction .* confirms|marked the invoice paid|has been completed)\b", re.I)),
    ("payment_promise", re.compile(r"\b(?:will pay|will be paid|will be sent|scheduled|expects? to release|expected next|pending|upon acceptance|after (?:review|acceptance)|if (?:selected|approved|accepted))\b", re.I)),
    ("agreement", re.compile(r"\b(?:green light|authorised|authorized|proceed with|please proceed|is approved|we accept|we confirm|agree to|have a deal|approve(?:d)? .{0,30} quote|quote .{0,30} (?:approved|confirmed))\b", re.I)),
]

EVIDENCE_TERMS = re.compile(
    r"\b(?:budget|reward|offer|quote|accept|agree|approved|confirmed|deal|deliver|submitted|attached|complete|"
    r"pay|paid|payment|payout|transfer|credited|received|receipt|settlement|failed|reversed|rejected|declined|cancelled|pending)\w*\b",
    re.I,
)
CONDITION_TERMS = re.compile(r"\b(?:if|after|upon|subject to|when|provided that|pending)\b", re.I)


def _parse_number(raw: str) -> float:
    value = raw.replace(" ", "")
    if "," in value and "." not in value:
        tail = value.rsplit(",", 1)[1]
        value = value.replace(",", ".") if len(tail) <= 2 else value.replace(",", "")
    else:
        value = value.replace(",", "")
    return float(value)


def extract_amounts(text: str):
    found = []
    occupied = set()
    for pattern in AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            if any(i in occupied for i in range(*match.span())):
                continue
            a, b = match.groups()
            currency, raw = (a.upper(), b) if not a[:1].isdigit() else (b.upper(), a)
            currency = SYMBOLS.get(currency, currency)
            try:
                value = _parse_number(raw)
            except ValueError:
                continue
            if not (0 < value < 1_000_000_000):
                continue
            occupied.update(range(*match.span()))
            found.append({"currency": currency, "value": value, "raw": match.group(0), "span": list(match.span())})
    return sorted(found, key=lambda x: x["span"])


def evidence_sentences(text: str):
    spans = []
    start = 0
    for match in re.finditer(r"(?<=[.!?])\s+|\n+", text):
        end = match.start()
        sentence = text[start:end].strip()
        if sentence and (EVIDENCE_TERMS.search(sentence) or extract_amounts(sentence)):
            spans.append({"quote": sentence, "start": start, "end": end})
        start = match.end()
    sentence = text[start:].strip()
    if sentence and (EVIDENCE_TERMS.search(sentence) or extract_amounts(sentence)):
        spans.append({"quote": sentence, "start": start, "end": len(text)})
    return spans[:8]


class IntentModel:
    def __init__(self):
        rows = training_rows()
        features = FeatureUnion([
            ("word", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, min_df=1)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True, min_df=1)),
        ])
        self.features = features
        x = features.fit_transform([text for _, text in rows])
        self.model = LogisticRegression(max_iter=1500, class_weight="balanced", random_state=17)
        self.model.fit(x, [label for label, _ in rows])
        self.feature_names = features.get_feature_names_out()
        self.fit_examples = len(rows)

    def predict(self, text: str):
        vector = self.features.transform([text])
        probabilities = self.model.predict_proba(vector)[0]
        order = np.argsort(probabilities)[::-1]
        class_index = int(order[0])
        weighted = vector.multiply(self.model.coef_[class_index]).tocoo()
        supporting = sorted(
            (
                (float(value), str(self.feature_names[column]).replace("word__", "word: ").replace("char__", "char: "))
                for column, value in zip(weighted.col, weighted.data)
                if value > 0
            ),
            reverse=True,
        )[:5]
        return {
            "label": str(self.model.classes_[order[0]]),
            "score_not_calibrated": round(float(probabilities[order[0]]), 4),
            "margin_not_calibrated": round(float(probabilities[order[0]] - probabilities[order[1]]), 4),
            "runner_up": str(self.model.classes_[order[1]]),
            "top_supporting_features": [
                {"feature": feature, "contribution": round(value, 5)}
                for value, feature in supporting
            ],
        }


@dataclass
class Message:
    id: str
    thread_id: str
    timestamp: str
    sender: str
    body: str
    subject: str = ""
    confirmation: str | None = None


def validate_messages(payload) -> list[Message]:
    if not isinstance(payload, list) or not (1 <= len(payload) <= MAX_MESSAGES):
        raise ValueError(f"input must be a nonempty list with at most {MAX_MESSAGES} messages")
    result, ids = [], set()
    for i, raw in enumerate(payload):
        if not isinstance(raw, dict):
            raise ValueError(f"message {i} must be an object")
        values = {}
        for field in ("id", "thread_id", "timestamp", "sender", "body"):
            value = raw.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"message {i} field {field} must be a nonempty string")
            values[field] = value.strip()
        if values["id"] in ids:
            raise ValueError("message ids must be unique")
        ids.add(values["id"])
        if len(values["body"]) > MAX_TEXT:
            raise ValueError(f"message {values['id']} exceeds {MAX_TEXT} characters")
        try:
            datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"message {values['id']} timestamp must be ISO 8601") from exc
        confirmation = raw.get("confirmation")
        if confirmation is not None and confirmation not in TRUSTED_CONFIRMATIONS:
            raise ValueError(f"message {values['id']} has unsupported confirmation value")
        result.append(Message(**values, subject=str(raw.get("subject", ""))[:300], confirmation=confirmation))
    return result


def _guard(text: str):
    for label, pattern in GUARDS:
        match = pattern.search(text)
        if match:
            return label, match.group(0)
    return None, None


def analyze_message(message: Message, model: IntentModel):
    combined = (message.subject + ". " + message.body).strip(". ")
    ml = model.predict(combined)
    guard_label, guard_quote = _guard(combined)
    low_information = ml["margin_not_calibrated"] < 0.03
    final = guard_label or ("needs_review" if low_information else ml["label"])
    operational = bool(workflow_signals([asdict(message)])) and not guard_label
    if operational:
        final = "admin"  # Explicit workflow update is not a payment failure.
    confirmed = bool(message.confirmation in TRUSTED_CONFIRMATIONS and final == "payment_notice")
    amounts = []
    seen_amounts = set()
    for amount in extract_amounts(combined):
        key = (amount["currency"], amount["value"])
        if key not in seen_amounts:
            amounts.append(amount)
            seen_amounts.add(key)
    return {
        **asdict(message),
        "text_sha256": hashlib.sha256(message.body.encode()).hexdigest(),
        "ml": ml,
        "status": final,
        "decision_basis": "deterministic_guard" if guard_label else "workflow_rule" if operational else "abstention" if low_information else "local_model",
        "model_guard_disagreement": bool(guard_label and guard_label != ml["label"]),
        "guard": {"applied": bool(guard_label), "label": guard_label, "matched": guard_quote},
        "amounts": amounts,
        "conditions": sorted(set(m.group(0).lower() for m in CONDITION_TERMS.finditer(combined))),
        "evidence": evidence_sentences(combined),
        "confirmed_received_from_input": confirmed,
        "confirmation_source": message.confirmation,
    }


def _chain_payload(event, previous_hash):
    """Canonical, body-free record used for the tamper-evident local chain."""
    return {
        "id": event["id"],
        "thread_id": event["thread_id"],
        "timestamp": event["timestamp"],
        "sender": event["sender"],
        "subject": event["subject"],
        "text_sha256": event["text_sha256"],
        "status": event["status"],
        "confirmation_source": event["confirmation_source"],
        "previous_event_sha256": previous_hash,
    }


def add_integrity_chain(events):
    """Link chronological events so later edits are detectable in exported JSON."""
    previous = "0" * 64
    for event in sorted(events, key=lambda x: (x["timestamp"], x["id"])):
        canonical = json.dumps(
            _chain_payload(event, previous), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        current = hashlib.sha256(canonical).hexdigest()
        event["integrity"] = {
            "previous_event_sha256": previous,
            "event_chain_sha256": current,
        }
        previous = current
    return previous


def verify_report_integrity(report):
    failures = []
    for thread in report.get("threads", []):
        previous = "0" * 64
        timeline = sorted(thread.get("timeline", []), key=lambda x: (x.get("timestamp", ""), x.get("id", "")))
        for event in timeline:
            integrity = event.get("integrity", {})
            canonical = json.dumps(
                _chain_payload(event, previous), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            expected = hashlib.sha256(canonical).hexdigest()
            if integrity.get("previous_event_sha256") != previous or integrity.get("event_chain_sha256") != expected:
                failures.append({"thread_id": thread.get("thread_id"), "event_id": event.get("id")})
            previous = expected
        if thread.get("integrity", {}).get("chain_head_sha256") != previous:
            failures.append({"thread_id": thread.get("thread_id"), "event_id": "chain_head"})
    return {"verified": not failures, "failures": failures, "threads_checked": len(report.get("threads", []))}


def case_evidence_packet(report, thread_id):
    """Create a portable, read-only evidence packet for one work case."""
    matches = [thread for thread in report.get("threads", []) if thread.get("thread_id") == thread_id]
    if not matches:
        raise ValueError(f"unknown thread id: {thread_id}")
    case = matches[0]
    packet = {
        "schema": "pactsignal-case-evidence-v1",
        "generated_at": report.get("generated_at"),
        "thread_id": thread_id,
        "stage": case["stage"],
        "agreed_by_currency": case["agreed_by_currency"],
        "promised_by_currency": case["promised_by_currency"],
        "promised_due_at": case.get("promised_due_at"),
        "promise_overdue": case.get("promise_overdue", False),
        "confirmed_received_by_currency": case["confirmed_received_by_currency"],
        "next_action": case["next_action"],
        "risks": case["risks"],
        "linked_receipts": case.get("linked_receipts", []),
        "integrity": case["integrity"],
        "timeline": case["timeline"],
        "limits": report.get("limits", []),
    }
    packet["packet_sha256"] = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return packet


def _sum_by_currency(events, predicate):
    totals = defaultdict(float)
    for event in events:
        if predicate(event):
            for amount in event["amounts"]:
                totals[amount["currency"]] += amount["value"]
    return {currency: round(value, 2) for currency, value in sorted(totals.items())}


def _sum_thread_money(threads, stages):
    totals = defaultdict(float)
    for thread in threads:
        if thread["stage"] not in stages:
            continue
        source = thread["agreed_by_currency"] or thread["promised_by_currency"]
        for currency, value in source.items():
            totals[currency] += value
    return {currency: round(value, 2) for currency, value in sorted(totals.items())}


def _name_tokens(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿА-Яа-яЁёІіЇїЄєҐґ]{3,}", text)
        if token.lower() not in NAME_STOPWORDS
    }


def _promise_due_date(event):
    """Extract a deliberately small set of unambiguous English due dates."""
    text = f"{event.get('subject', '')} {event.get('body', '')}".lower()
    base = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).date()
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if explicit:
        try:
            return datetime.fromisoformat(explicit.group(1)).date().isoformat()
        except ValueError:
            return None
    if re.search(r"\btomorrow\b", text):
        return (base + timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", text):
        return base.isoformat()
    return None


def reconcile_provider_receipts(threads):
    """Link authenticated provider receipts to work threads conservatively.

    Payment notifications normally arrive in a different email thread from
    the client conversation.  A receipt is linked only when a named payer is
    visible in its text and one candidate thread has a strictly better token
    match.  Ambiguous receipts stay unlinked and therefore cannot silently
    turn a client case into paid income.
    """
    work_threads = []
    receipts = []
    for thread in threads:
        providers = [
            event for event in thread["timeline"]
            if any(marker in event["sender"].lower() for marker in PAYMENT_PROVIDER_MARKERS)
        ]
        confirmed = [event for event in providers if event["confirmed_received_from_input"]]
        if confirmed:
            receipts.extend((thread, event) for event in confirmed)
        else:
            work_threads.append(thread)

    for receipt_thread, receipt in receipts:
        receipt_tokens = _name_tokens(receipt["body"] + " " + receipt["subject"])
        scored = []
        for candidate in work_threads:
            party_tokens = set()
            for event in candidate["timeline"]:
                if not any(marker in event["sender"].lower() for marker in PAYMENT_PROVIDER_MARKERS):
                    party_tokens |= _name_tokens(event["sender"])
            overlap = receipt_tokens & party_tokens
            if not overlap:
                continue
            score = len(overlap) * 3
            receipt_amounts = {(a["currency"], a["value"]) for a in receipt["amounts"]}
            case_amounts = {
                (currency, value)
                for bucket in (candidate["agreed_by_currency"], candidate["promised_by_currency"])
                for currency, value in bucket.items()
            }
            # A name alone is not an invoice match. Leave partial, converted or
            # earlier receipts unallocated for explicit review.
            if not receipt_amounts or receipt_amounts != case_amounts:
                continue
            if datetime.fromisoformat(receipt["timestamp"].replace("Z", "+00:00")) < datetime.fromisoformat(candidate["timeline"][0]["timestamp"].replace("Z", "+00:00")):
                continue
            score += 3 if receipt_amounts & case_amounts else 0
            if receipt["timestamp"] >= candidate["timeline"][0]["timestamp"]:
                score += 1
            scored.append((score, candidate, sorted(overlap)))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored or scored[0][0] < 4 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
            receipt_thread.setdefault("risks", []).append("authenticated_receipt_not_uniquely_linked")
            receipt_thread['stage'] = 'receipt_unallocated'
            receipt_thread['next_action'] = 'Receipt imported, but its order is ambiguous. Check the payment reference before allocating it; no client order is marked paid.'
            continue
        score, target, overlap = scored[0]
        target.setdefault("linked_receipts", []).append({
            "event_id": receipt["id"],
            "source_thread_id": receipt_thread["thread_id"],
            "source_sender": receipt["sender"],
            "source_subject": receipt["subject"],
            "source_body": receipt["body"],
            "source_text_sha256": receipt["text_sha256"],
            "confirmation_source": receipt["confirmation_source"],
            "match_score": score,
            "matching_name_tokens": overlap,
            "amounts": receipt["amounts"],
            "timestamp": receipt["timestamp"],
        })
        for amount in receipt["amounts"]:
            currency = amount["currency"]
            target["confirmed_received_by_currency"][currency] = round(
                target["confirmed_received_by_currency"].get(currency, 0) + amount["value"], 2
            )
        if target["agreed_by_currency"] and not (
            set(target["agreed_by_currency"]) & set(target["confirmed_received_by_currency"])
        ):
            target["risks"].append("receipt_currency_differs_from_agreement_review_conversion")
        needs_review = any(abs(target["confirmed_received_by_currency"].get(c, 0) - v) > 0.005 for c, v in target["agreed_by_currency"].items()) or "payment_failure_or_reversal_detected" in target["risks"]
        target["stage"] = "payment_issue" if needs_review else "confirmed_received"
        target["next_action"] = "Review the payment discrepancy or reversal before closing this case." if needs_review else "Receipt linked by payer evidence. Review the match, then close or archive."
        target["risks"] = [
            risk for risk in target["risks"]
            if risk not in (
                "payment_promised_but_not_confirmed_received",
                "promised_payment_overdue",
                "accepted_work_not_paid",
            )
        ]
        receipt_thread["stage"] = "reconciled_receipt"
        receipt_thread["next_action"] = f"Receipt linked to {target['thread_id']}; review the payer match."
    return threads


def aggregate_thread(thread_id: str, events):
    events = sorted(events, key=lambda x: x["timestamp"])
    chain_head = add_integrity_chain(events)
    agreements = [e for e in events if e["status"] == "agreement"]
    acceptances = [e for e in events if e["status"] == "acceptance"]
    promises = [e for e in events if e["status"] == "payment_promise"]
    notices = [e for e in events if e["status"] == "payment_notice"]
    failures = [e for e in events if e["status"] == "payment_failure"]
    delivered = any(e["status"] == "delivery" for e in events)
    # A later explicit agreement reopens a previously rejected conversation.
    decisions = [e for e in events if e["status"] in ("rejection", "agreement")]
    rejected = bool(decisions and decisions[-1]["status"] == "rejection")
    confirmed = [e for e in notices if e["confirmed_received_from_input"]]
    agreed = _sum_by_currency(agreements, lambda _: True)
    confirmed_totals = _sum_by_currency(confirmed, lambda _: True)
    promised = _sum_by_currency(promises, lambda _: True)
    promised_due_at = _promise_due_date(promises[-1]) if promises else None
    today = datetime.now(timezone.utc).date()
    promise_overdue = bool(promised_due_at and datetime.fromisoformat(promised_due_at).date() < today and not confirmed)
    risks = []
    if not agreements and any(e["status"] in ("delivery", "payment_promise") for e in events):
        risks.append("work_or_payment_discussed_without_detected_agreement")
    if delivered and not agreements:
        risks.append("delivery_without_detected_price_agreement")
    if promises and not confirmed:
        risks.append("payment_promised_but_not_confirmed_received")
    if promise_overdue:
        risks.append("promised_payment_overdue")
    if notices and not confirmed:
        risks.append("payment_notice_requires_independent_confirmation")
    if failures:
        risks.append("payment_failure_or_reversal_detected")
    if acceptances and not confirmed:
        risks.append("accepted_work_not_paid")
    for currency, value in agreed.items():
        paid = confirmed_totals.get(currency, 0)
        if confirmed and abs(paid - value) > 0.005:
            risks.append(f"confirmed_{currency}_amount_differs_from_detected_agreement")
    stage = "rejected" if rejected else "confirmed_received" if confirmed else "payment_issue" if failures else "payment_notice" if notices else "payment_promised" if promises else "accepted" if acceptances else "delivered" if delivered else "agreed" if agreements else "open"
    if confirmed and (failures or any(abs(confirmed_totals.get(c, 0) - v) > 0.005 for c, v in agreed.items()) or (agreed and set(confirmed_totals) - set(agreed))):
        stage = "payment_issue"
    next_action = {
        "confirmed_received": "No payment action. Preserve the evidence and close or archive when ready.",
        "payment_issue": "Review the partial, mismatched, failed or reversed payment before closing this case.",
        "payment_notice": "Verify the money independently; a notice alone is not confirmed income.",
        "payment_promised": "Track the promised date and avoid unapproved additional work until receipt.",
        "accepted": "Accepted, but no payment is confirmed. Request payment status and a viable route before unpaid extensions.",
        "delivered": "Request explicit acceptance and payment status for the agreed work.",
        "agreed": "Track delivery scope and preserve the agreed amount before starting extra work.",
        "rejected": "Close the opportunity unless a new, explicit offer arrives.",
        "open": "Prepare or decline the requested quote; do not count the budget ceiling as agreed revenue.",
    }[stage]
    if stage == "payment_promised" and promised_due_at:
        timing = "overdue" if promise_overdue else "due today" if datetime.fromisoformat(promised_due_at).date() == today else "scheduled"
        next_action = f"Payment is {timing} for {promised_due_at}. Verify receipt before additional unpaid work."
    return {
        "thread_id": thread_id,
        "stage": stage,
        "agreed_by_currency": agreed,
        "promised_by_currency": promised,
        "promised_due_at": promised_due_at,
        "promise_overdue": promise_overdue,
        "confirmed_received_by_currency": confirmed_totals,
        "risks": list(dict.fromkeys(risks)),
        "next_action": next_action,
        "integrity": {"algorithm": "SHA-256", "chain_head_sha256": chain_head},
        "timeline": events,
    }


def analyze(payload, model=None):
    messages = validate_messages(payload)
    model = model or IntentModel()
    events = [analyze_message(m, model) for m in messages]
    grouped = defaultdict(list)
    for event in events:
        grouped[event["thread_id"]].append(event)
    threads = annotate_threads(reconcile_provider_receipts([aggregate_thread(key, grouped[key]) for key in sorted(grouped)]))
    totals = _sum_by_currency([e for e in events if e["confirmed_received_from_input"]], lambda _: True)
    work_threads = [t for t in threads if t["stage"] != "reconciled_receipt"]
    evidence_threads = [t for t in threads if t["stage"] == "reconciled_receipt"]
    actionable = [t for t in work_threads if t["stage"] not in ("confirmed_received", "rejected")]
    at_risk = [t for t in work_threads if t["stage"] in ("payment_issue", "payment_notice", "payment_promised", "accepted", "receipt_unallocated")]
    result = {
        "schema": "pactsignal-analysis-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "model": {"type": "local scikit-learn TF-IDF word+character LogisticRegression", "fit_examples": model.fit_examples, "scores_calibrated": False},
        "summary": {
            "messages": len(events),
            "threads": len(work_threads),
            "evidence_sources": len(evidence_threads),
            "needs_review": sum(e["status"] == "needs_review" for e in events),
            "actionable_threads": len(actionable),
            "at_risk_threads": len(at_risk),
            "awaiting_reply_threads": sum(t["stage"] == "delivered" for t in work_threads),
            "awaiting_payment_threads": sum(
                t["stage"] in ("payment_issue", "payment_notice", "payment_promised", "accepted", "receipt_unallocated")
                for t in work_threads
            ),
            "active_work_threads": sum(t["stage"] in ("agreed", "open") for t in work_threads),
            "closed_threads": sum(
                t["stage"] in ("confirmed_received", "rejected")
                for t in work_threads
            ),
            "unresolved_value_by_currency": _sum_thread_money(
                work_threads, {"payment_issue", "payment_notice", "payment_promised", "accepted", "delivered"}
            ),
            "promised_by_currency": _sum_by_currency(
                [e for e in events if e["status"] == "payment_promise"], lambda _: True
            ),
            "confirmed_received_by_currency": totals,
        },
        "threads": threads,
        "limits": [
            "Synthetic training data; no real-world accuracy claim.",
            "Classification does not authenticate messages, agreements or payments.",
            "A payment enters confirmed totals only when the input explicitly carries an allowed confirmation assertion.",
            "Review exact quoted evidence and the original source before acting.",
        ],
    }
    result["integrity_check"] = verify_report_integrity(result)
    return result


def evaluate(model=None):
    model = model or IntentModel()
    rows = []
    for expected, text in STRESS_ROWS + ADVERSARIAL_ROWS:
        msg = Message(id=f"eval-{len(rows)+1}", thread_id="eval", timestamp="2026-01-01T00:00:00Z", sender="synthetic", body=text)
        result = analyze_message(msg, model)
        guard_label = result["guard"]["label"] or "needs_review"
        rows.append({"expected": expected, "text": text, "actual": result["status"], "ml": result["ml"]["label"], "rules_only": guard_label, "guard": result["guard"]})
    metamorphic = evaluate_metamorphic(model)
    return {
        "training_examples": model.fit_examples,
        "stress_examples": len(rows),
        "rules_only_correct": sum(r["rules_only"] == r["expected"] for r in rows),
        "model_only_correct": sum(r["ml"] == r["expected"] for r in rows),
        "guarded_pipeline_correct": sum(r["actual"] == r["expected"] for r in rows),
        # Backwards-compatible names retained for existing integrations.
        "ml_correct": sum(r["ml"] == r["expected"] for r in rows),
        "final_correct": sum(r["actual"] == r["expected"] for r in rows),
        "abstentions": sum(r["actual"] == "needs_review" for r in rows),
        "auto_routed": sum(r["actual"] != "needs_review" for r in rows),
        "wrong_non_abstained": sum(r["actual"] not in ("needs_review", r["expected"]) for r in rows),
        "metamorphic_checks": metamorphic["checks"],
        "metamorphic_passed": metamorphic["passed"],
        "metamorphic_failures": metamorphic["failures"],
        "rows": rows,
        "limits": "All examples are AI-authored synthetic engineering data with different templates. This is not an independent or real-world benchmark.",
    }


def evaluate_metamorphic(model=None):
    """Check stability under irrelevant context and sensitivity to meaning changes."""
    model = model or IntentModel()
    bases = [
        ("offer", "The available reward is 90 USD for the first accepted result."),
        ("agreement", "We approve your quote for 90 USD. Please proceed."),
        ("delivery", "The final report has been delivered for review."),
        ("payment_promise", "We will pay 90 USD after acceptance."),
        ("payment_notice", "Payment completed successfully for 90 USD."),
        ("payment_failure", "Payment of 90 USD failed and no funds arrived."),
        ("rejection", "The proposal was declined and we will not proceed."),
        ("admin", "Support ticket 17 was routed to the billing team."),
    ]
    neutral = [
        " Reference code AZ-17.",
        " This note was generated on Thursday.",
        " Please keep the project title unchanged.",
        " The message contains two short paragraphs.",
        " Archive label: October batch.",
    ]
    failures = []
    checks = 0
    for expected, base in bases:
        original = analyze_message(Message(f"m-{checks}", "meta", "2026-01-01T00:00:00Z", "synthetic", base), model)["status"]
        for suffix in neutral:
            checks += 1
            actual = analyze_message(Message(f"m-{checks}", "meta", "2026-01-01T00:00:00Z", "synthetic", base + suffix), model)["status"]
            if original != expected or actual != expected:
                failures.append({"expected": expected, "original": original, "actual": actual, "suffix": suffix})
    contrasts = [
        ("payment_promise", "We will pay 90 USD tomorrow."),
        ("payment_failure", "Payment of 90 USD failed and no funds arrived."),
        ("payment_notice", "Payment completed successfully for 90 USD."),
        ("offer", "The reward is 90 USD for the first accepted result."),
        ("agreement", "We approve the 90 USD quote. Please proceed."),
        ("rejection", "The 90 USD proposal was declined; do not proceed."),
        ("delivery", "The final files were delivered for review."),
        ("admin", "Support ticket 90 was routed to billing."),
    ]
    for expected, text in contrasts:
        checks += 1
        actual = analyze_message(Message(f"c-{checks}", "meta", "2026-01-01T00:00:00Z", "synthetic", text), model)["status"]
        if actual != expected:
            failures.append({"expected": expected, "actual": actual, "text": text})
    return {"checks": checks, "passed": checks - len(failures), "failures": failures}


def _money(totals):
    return " · ".join(f"{value:,.2f} {currency}" for currency, value in totals.items()) or "—"


STAGE_COPY = {
    "receipt_unallocated": ("Receipt needs allocation", "amber"),
    "confirmed_received": ("Settlement confirmed", "green"),
    "reconciled_receipt": ("Receipt matched", "green"),
    "payment_issue": ("Payment problem", "red"),
    "payment_notice": ("Verify payment", "amber"),
    "payment_promised": ("Promised, not received", "amber"),
    "accepted": ("Accepted · no receipt", "blue"),
    "delivered": ("Awaiting acceptance", "blue"),
    "agreed": ("Work agreed", "blue"),
    "rejected": ("Closed", "gray"),
    "open": ("Terms unclear", "gray"),
}


def _thread_title(thread):
    """Prefer a human-readable correspondent over an internal thread id."""
    for event in thread["timeline"]:
        sender = event.get("sender", "").strip()
        if sender and not any(marker in sender.lower() for marker in PAYMENT_PROVIDER_MARKERS):
            return sender
    return thread["thread_id"]


def render_html(report, interactive=False, showcase=False):
    esc = lambda x: html.escape(str(x), quote=True)
    cards = []
    queue = []
    priorities = {"payment_issue": 0, "payment_notice": 1, "accepted": 2, "payment_promised": 3, "delivered": 4, "agreed": 5, "open": 6}
    status_groups = {
        "receipt_unallocated": "payment",
        "payment_issue": "payment",
        "payment_notice": "payment",
        "payment_promised": "payment",
        "accepted": "payment",
        "delivered": "reply",
        "agreed": "active",
        "open": "active",
        "confirmed_received": "closed",
        "reconciled_receipt": "closed",
        "rejected": "closed",
    }
    for index, thread in enumerate(report["threads"]):
        if thread["stage"] == "reconciled_receipt":
            continue
        timeline = []
        for e in thread["timeline"]:
            amounts = _money({a["currency"]: a["value"] for a in e["amounts"]})
            evidence = "".join(f"<blockquote>{esc(q['quote'])}</blockquote>" for q in e["evidence"]) or "<blockquote>No financial evidence phrase extracted.</blockquote>"
            confirmation = f"<span class='pill green'>confirmed from input: {esc(e['confirmation_source'])}</span>" if e["confirmed_received_from_input"] else ""
            confirm_form = ""
            if interactive and e["status"] == "payment_notice" and not e["confirmed_received_from_input"]:
                confirm_form = f"<form method='post' action='/confirm'><input type='hidden' name='message_id' value='{esc(e['id'])}'><button class='confirm'>I independently confirmed receipt</button></form>"
            event_name = e["status"].replace("_", " ").title()
            timeline.append(f"<article class='event'><header><span>{esc(e['timestamp'])}</span><strong>{esc(event_name)}</strong></header><p class='mailmeta'><b>{esc(e['sender'])}</b><span>{esc(e['subject'])}</span></p><p class='messagebody'>{esc(e['body'])}</p><p class='amount'>{esc(amounts)}</p>{confirmation}{confirm_form}<details><summary>Why PactSignal classified this · Evidence &amp; model trace</summary>{evidence}<p><b>Decision basis:</b> {esc(e['decision_basis'])}{' · model/guard disagreement retained' if e['model_guard_disagreement'] else ''}</p><pre>{esc(json.dumps(e['ml'], indent=2))}</pre><small>Body SHA-256 {esc(e['text_sha256'])}</small><br><small>Event-chain SHA-256 {esc(e['integrity']['event_chain_sha256'])}</small></details></article>")
        risks = "".join(f"<li>{esc(r.replace('_',' '))}</li>" for r in thread["risks"]) or "<li>No configured risk triggered; review still required.</li>"
        stage_name, color = STAGE_COPY[thread["stage"]]
        linked = "".join(
            f"<details class='receiptproof'><summary>{esc(_money({a['currency']: a['value'] for a in item['amounts']}))} settled · {esc(item['timestamp'])} · open payment proof</summary><p><b>Source:</b> {esc(item['source_sender'])} · {esc(item['source_subject'])}</p><p class='messagebody'>{esc(item['source_body'])}</p><p><b>Independent confirmation:</b> {esc(item['confirmation_source'])}</p><p><b>Reconciliation:</b> exact amount/currency + payer tokens {esc(', '.join(item['matching_name_tokens']))} + chronology · internal score {item['match_score']}</p><small>Evidence ID {esc(item['event_id'])} · Body SHA-256 {esc(item['source_text_sha256'])}</small></details>"
            for item in thread.get("linked_receipts", [])
        )
        linked_html = f"<h3>Verified payment evidence</h3>{linked}" if linked else ""
        title = _thread_title(thread)
        workflow_html = ''.join(f"<div class='workflow-signal'><b>{esc(s['label'])}</b><blockquote>{esc(s['quote'])}</blockquote><small>Explicit phrase · {esc(s['timestamp'])} · owner review required</small></div>" for s in thread.get('workflow_signals', []))
        card_id = f"case-{index + 1}"
        group = status_groups.get(thread["stage"], "active")
        data_risk = "true" if thread["stage"] in ("payment_issue", "payment_notice", "payment_promised", "accepted", "receipt_unallocated") else "false"
        data_actionable = "false" if group == "closed" else "true"
        last_event = thread["timeline"][-1]
        searchable = " ".join(
            [title, thread["thread_id"], thread["stage"]]
            + [f"{e['sender']} {e['subject']} {e['body']}" for e in thread["timeline"]]
        ).lower()
        if thread["stage"] not in ("confirmed_received", "reconciled_receipt", "rejected"):
            queue.append((priorities.get(thread["stage"], 9), f"<li><span class='stage {color}'>{esc(stage_name)}</span><div><b>{esc(title)}</b><p>{esc(thread['next_action'])}</p></div><strong>{esc(_money(thread['agreed_by_currency'] or thread['promised_by_currency']))}</strong><a class='openlink' href='#{card_id}'>Open case</a></li>"))
        cards.append(f"<details id='{card_id}' class='thread' data-received='{str(bool(thread["confirmed_received_by_currency"])).lower()}' data-group='{group}' data-stage='{esc(thread['stage'])}' data-risk='{data_risk}' data-actionable='{data_actionable}' data-search='{esc(searchable)}'><summary class='threadtop'><div><small>{"PAYMENT TO ALLOCATE" if thread["stage"] == "receipt_unallocated" else "CLIENT CASE"}</small><h2>{esc(title)}</h2><p>{esc(thread["timeline"][0]["subject"])}</p><small>Last message · {esc(last_event['timestamp'])} · {len(thread['timeline'])} message{'s' if len(thread['timeline']) != 1 else ''}</small></div><span class='stage {color}'>{esc(stage_name)}</span></summary><div class='moneygrid'><div><small>AGREED</small><b>{esc(_money(thread['agreed_by_currency']))}</b></div><div><small>PROMISED</small><b>{esc(_money(thread['promised_by_currency']))}</b></div><div><small>CONFIRMED RECEIVED</small><b>{esc(_money(thread['confirmed_received_by_currency']))}</b></div></div><div class='action'><small>OWNER DECISION</small><b>{esc(thread['next_action'])}</b></div>{workflow_html}{linked_html}<details class='conversation' open><summary>Open correspondence ({len(thread['timeline'])})</summary><div class='timeline'>{''.join(timeline)}</div></details><details class='technical'><summary>Timeline &amp; technical evidence: risks and integrity</summary><h3>Review signals</h3><ul>{risks}</ul><small>CHAIN HEAD {esc(thread['integrity']['chain_head_sha256'])}</small></details></details>")
    confirmed = esc(_money(report["summary"]["confirmed_received_by_currency"]))
    promised = esc(_money(report["summary"].get("promised_by_currency", {})))
    unresolved = esc(_money(report["summary"].get("unresolved_value_by_currency", {})))
    queue_html = "".join(markup for _, markup in sorted(queue, key=lambda item: item[0]))
    mode = "PUBLIC SHOWCASE · FICTIONAL DATA" if showcase else "LOCAL · READ ONLY"
    demo_note = "Fictional scenarios · Precomputed by the real local analyzer. Explore messages and decision evidence; no live mailbox or bank connection." if showcase else "Your evidence stays on this device."
    return f"""<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>PactSignal · owner control room</title><style>
:root{{--ink:#152437;--muted:#627186;--line:#d9e4e8;--panel:#fff;--canvas:#f3f7f6;--nav:#10283a;--aqua:#087f74;--aqua2:#14a394;--accent:#f36b3b;--shadow:0 18px 55px #18394a12}}*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 92% 4%,#c9eee8 0,transparent 24rem),var(--canvas);color:var(--ink);font:15px/1.55 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}button,input{{font:inherit}}nav{{background:linear-gradient(125deg,#071b29 0%,#0b3544 58%,#0a5b59 100%);color:white;padding:16px max(20px,5vw);display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:4;box-shadow:0 12px 30px #071b2925}}.brand{{display:flex;gap:12px;align-items:center}}.mark{{display:grid;place-items:center;width:42px;height:42px;border-radius:13px;background:linear-gradient(145deg,#25cfb6,#f9c76a);color:#082330;font-weight:950;font-size:21px;box-shadow:inset 0 1px #ffffff77}}nav h1{{margin:0;font-size:23px;letter-spacing:-.02em}}nav p{{margin:0;color:#cbe1e5;font-size:13px}}.mode{{padding:8px 11px;border:1px solid #ffffff35;border-radius:999px;background:#ffffff12;font-size:11px;letter-spacing:.08em}}main{{max-width:1180px;margin:0 auto;padding:38px 20px 24px}}.hero{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(280px,.8fr);gap:24px;align-items:end;margin-bottom:24px}}.eyebrow{{color:var(--aqua);font-weight:900;letter-spacing:.1em;font-size:12px}}.ownerhead h1{{font-size:clamp(38px,5.5vw,66px);line-height:.98;letter-spacing:-.055em;margin:8px 0 16px;max-width:780px}}.ownerhead p{{font-size:18px;color:var(--muted);margin:0;max-width:730px}}.judgecard{{background:#0d2f3b;color:white;border-radius:22px;padding:21px;box-shadow:var(--shadow);border:1px solid #ffffff22}}.judgecard small{{color:#9fd9d2}}.judgecard h2{{font-size:20px;margin:5px 0 13px}}.judgecard ol{{margin:0;padding-left:19px;color:#d5e7ea}}.judgecard li+li{{margin-top:7px}}.judgecard button{{margin-top:16px;width:100%;border:0;border-radius:11px;padding:11px;background:#f5c970;color:#17313c;font-weight:850;cursor:pointer}}.stats,.moneygrid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:20px 0}}button.metric,.moneygrid>div{{background:#ffffffdd;backdrop-filter:blur(10px);border:1px solid var(--line);padding:17px;border-radius:16px;text-align:left;color:inherit}}button.metric{{cursor:pointer;box-shadow:0 8px 28px #253b4d0a;transition:transform .18s,border-color .18s,box-shadow .18s}}button.metric:hover{{transform:translateY(-2px)}}button.metric.active{{border-color:var(--aqua);box-shadow:0 0 0 3px #087f741b,0 12px 32px #087f7414}}small{{display:block;color:var(--muted);letter-spacing:.075em;font-weight:800;font-size:11px}}.metric b,.stats b{{display:block;font-size:27px;margin-top:4px;letter-spacing:-.035em}}.metric em{{display:block;font-style:normal;color:var(--aqua);font-weight:750;font-size:12px;margin-top:4px}}.portfolio{{padding:13px 16px;background:#ffffff88;border:1px solid var(--line);border-radius:12px}}.sectionhead{{display:flex;justify-content:space-between;align-items:end;gap:16px;margin-top:29px}}.sectionhead h3{{margin:0;font-size:21px}}.sectionhead p{{margin:0;color:var(--muted)}}.queue{{list-style:none;padding:0;margin:13px 0 30px}}.queue li{{display:grid;grid-template-columns:175px 1fr auto auto;align-items:center;gap:15px;background:white;border:1px solid var(--line);padding:16px;border-radius:15px;margin:9px 0;box-shadow:0 6px 22px #253b4d09;transition:transform .18s,box-shadow .18s}}.queue li:hover{{transform:translateX(3px);box-shadow:0 10px 30px #253b4d12}}.queue p{{margin:3px 0;color:var(--muted)}}.openlink{{color:var(--aqua);font-weight:850;text-decoration:none;padding:7px 9px;border-radius:8px}}.openlink:hover{{background:#e8f7f4}}.warning{{background:linear-gradient(90deg,#fff8dd,#fffdf3);border:1px solid #ecd177;padding:16px 18px;border-radius:14px;box-shadow:0 8px 22px #9c76220a}}.privacy{{margin-top:12px;padding:0 4px}}.privacy summary{{font-weight:800;color:#285b69}}.toolbar{{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:28px 0 12px;padding:10px;background:#e8efef;border:1px solid #d7e2e4;border-radius:17px;position:sticky;top:82px;z-index:3}}.toolbar button{{border:1px solid transparent;background:transparent;border-radius:999px;padding:8px 12px;font-weight:780;cursor:pointer;color:#45596b}}.toolbar button:hover{{background:#fff}}.toolbar button.active{{background:#173f49;color:white;border-color:#173f49;box-shadow:0 5px 14px #173f4925}}.toolbar input{{margin-left:auto;min-width:260px;border:1px solid var(--line);border-radius:10px;padding:9px 12px;background:white}}.resultcount{{color:var(--muted);margin:0 0 10px}}.thread{{display:block;background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:21px;margin:16px 0;box-shadow:var(--shadow);scroll-margin-top:155px}}.thread[hidden]{{display:none}}.thread>summary{{list-style:none}}.thread>summary::-webkit-details-marker{{display:none}}.threadtop,.event header{{display:flex;justify-content:space-between;gap:15px;align-items:center}}.threadtop h2{{font-size:24px;letter-spacing:-.025em}}h2{{margin:0}}h3{{margin-top:26px}}.stage,.pill{{padding:7px 11px;border-radius:999px;font-weight:800;white-space:nowrap;font-size:12px}}.green{{background:#dff5eb;color:#08764d}}.red{{background:#fee2e2;color:#a42121}}.amber{{background:#fff0c7;color:#875d00}}.blue{{background:#dcecff;color:#155da0}}.gray{{background:#e8edf1;color:#586773}}.action{{padding:16px;border-radius:13px;background:linear-gradient(100deg,#eaf8f6,#f7fffd);border:1px solid #bfe4de}}.conversation,.technical{{margin-top:22px}}.conversation>summary,.technical>summary{{color:#136f84;font-weight:850;font-size:16px}}.timeline{{border-left:3px solid #b9ceda;margin:20px 0 0 8px;padding-left:23px}}.event{{border:1px solid var(--line);border-radius:13px;padding:16px;margin:0 0 15px;background:#fbfdfe}}.event header strong{{color:#0b786b}}.mailmeta{{display:grid;gap:2px}}.mailmeta span{{color:var(--muted)}}.messagebody{{white-space:pre-wrap;background:white;border-radius:9px;padding:12px;border:1px solid #e7edf1}}blockquote{{margin:10px 0;padding:10px 14px;background:#edf7f5;border-left:4px solid var(--aqua)}}pre{{white-space:pre-wrap;background:#122331;color:#dbe9ef;padding:12px;border-radius:8px;overflow:auto}}.amount{{font-size:18px;font-weight:850}}details summary{{cursor:pointer}}button.confirm{{margin:8px 0;padding:9px 12px;border:0;border-radius:8px;background:#0b786b;color:white;font-weight:800;cursor:pointer}}footer{{margin-top:35px;padding:25px 0;color:var(--muted);border-top:1px solid var(--line)}}.demo-note{{font-weight:750;color:#2f6570}}@media(max-width:900px){{.hero{{grid-template-columns:1fr}}.stats{{grid-template-columns:1fr 1fr}}.queue li{{grid-template-columns:150px 1fr auto}}.queue .openlink{{grid-column:2}}}}@media(max-width:650px){{main{{padding-top:25px}}.stats,.moneygrid{{grid-template-columns:1fr 1fr}}.queue li{{grid-template-columns:1fr}}.queue .openlink{{grid-column:auto}}nav{{position:static;align-items:flex-start;gap:10px;flex-direction:column}}.toolbar{{position:static}}.toolbar input{{margin-left:0;width:100%;min-width:0}}.ownerhead h1{{font-size:39px}}.mode{{font-size:10px}}}}
.queue li>*{{min-width:0}}.queue .stage,.threadtop>.stage{{white-space:normal;overflow-wrap:anywhere;line-height:1.4;max-width:100%;display:inline-block}}.queue .openlink{{white-space:normal;max-width:180px}}.queue b,.threadtop h2{{overflow-wrap:anywhere}}.queue-channels{{display:block;font-size:12px;color:#087f74;font-weight:750;margin:4px 0}}</style><nav><div class='brand'><div class='mark'>P</div><div><h1>PactSignal</h1><p>Promises become decisions. Receipts become truth.</p></div></div><b class='mode'>{mode}</b></nav><main><button id="back-view" hidden type="button" title="Restore the previous filter, search and scroll position" style="position:fixed;bottom:18px;right:24px;z-index:8;background:#173f49;color:white;border:2px solid #9bd6ca;border-radius:12px;padding:12px 18px;box-shadow:0 4px 18px #173f4933;cursor:pointer">← Back to previous view</button><p><a class="scenario-tab" href="handoff.html">Email + messaging · manager handover →</a> <a class="scenario-tab" href="handoff-ru.html" lang="ru">Сценарий на русском →</a></p><section class='hero'><div class='ownerhead'><div class='eyebrow'>OWNER CONTROL ROOM · What needs your attention</div><h1>Know what is owed.<br>Know what is real.</h1><p>One decision queue for scattered client email—without sending messages or moving money.</p></div><aside class='judgecard'><small>60-SECOND PRODUCT TOUR</small><h2>See the trust boundary working</h2><ol><li>Open payment risk</li><li>Inspect the exact client message</li><li>Compare a promise with verified settlement evidence</li></ol><button id='tour'>Start with payment risk →</button></aside></section><p class='demo-note'>{demo_note}</p><div class='stats'><button class='metric active' data-filter='actionable'><small>NEEDS ACTION</small><b>{report['summary'].get('actionable_threads', 0)}</b><em>Open the decision queue</em></button><button class='metric' data-filter='risk'><small>PAYMENT AT RISK</small><b>{report['summary'].get('at_risk_threads', 0)}</b><em>Accepted without receipt or failed route</em></button><button class='metric' data-filter='reply'><small>WAITING ON CLIENT</small><b>{report['summary'].get('awaiting_reply_threads', 0)}</b><em>Delivered, no decision yet</em></button><button class='metric' data-filter='receipts'><small>CONFIRMED RECEIVED</small><b>{confirmed}</b><em>Includes unallocated receipts · inspect proof</em></button></div><p class='portfolio'><b>Open agreed value:</b> {unresolved} · currencies and lifecycle states never get blended into a misleading total</p><div class='sectionhead'><div><div class='eyebrow'>NEXT BEST ACTION</div><h3>Priority decisions</h3></div><p>Exceptions first. Evidence one click away.</p></div><ul class='queue'>{queue_html or '<li><b>Nothing needs action.</b><span>All visible cases are closed or reconciled.</span></li>'}</ul><div class='warning'><b>Trust boundary:</b> an email that says “paid” is still not money. PactSignal changes the received total only after independent confirmation, and every result opens back to the exact correspondence.</div><details class='privacy'><summary>Privacy and control</summary><p>Mailbox analysis, SQLite storage and the bundled model run locally. The core works with outbound network disabled. PactSignal never sends mail or moves money. Choose the mailbox folder or Gmail query you connect; remove the selected local database to delete the imported workspace. No telemetry is included in this build.</p></details><div id='results-start' aria-hidden='true' style='height:1px;overflow-anchor:none'></div><div class='toolbar' aria-label='Case filters'><button class='active' data-filter='actionable'>Needs action</button><button data-filter='all'>All cases</button><button data-filter='risk'>Payment risk</button><button data-filter='reply'>Waiting on client</button><button data-filter='payment'>Waiting for payment</button><button data-filter='active'>Active work</button><button data-filter='closed'>Closed</button><button data-filter='receipts'>Receipt evidence</button><input id='search' type='search' autocomplete='off' placeholder='Search client, subject or message…' aria-label='Search cases'></div><p class='resultcount' id='resultcount'></p><section id='cases'>{''.join(cards)}</section><p><b>Integrity chain:</b> {'verified locally' if report.get('integrity_check', {}).get('verified') else 'verification failed — review export'}. It detects later edits inside this export; it does not authenticate a sender.</p><footer>Local-first · read-only mailbox analysis · no automatic replies or financial actions · public showcase contains fictional data only</footer></main><script>
const cases=[...document.querySelectorAll('.thread')], buttons=[...document.querySelectorAll('[data-filter]')], search=document.querySelector('#search'), count=document.querySelector('#resultcount'), toolbar=document.querySelector('.toolbar'), back=document.querySelector('#back-view');let current='actionable';const views=[];
function remember(){{views.push({{filter:current,query:search.value,open:cases.filter(c=>c.open).map(c=>c.id),y:window.scrollY}});back.hidden=false}}
function matches(card,filter){{if(filter==='all')return true;if(filter==='actionable')return card.dataset.actionable==='true';if(filter==='risk')return card.dataset.risk==='true';if(filter==='receipts')return card.dataset.received==='true';return card.dataset.group===filter}}
const motion=window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth';
function revealResults(){{const header=document.querySelector('nav');toolbar.style.scrollMarginTop=((header?header.getBoundingClientRect().height:88)+12)+'px';const marker=document.querySelector('#results-start');window.scrollTo({{top:Math.max(0,window.scrollY+marker.getBoundingClientRect().top-(header?header.getBoundingClientRect().height:88)-12),behavior:'instant'}})}}
function apply(filter=current){{const area=document.querySelector('#cases');area.style.overflowAnchor='none';area.style.minHeight=window.innerHeight+'px';current=filter;const q=(search.value||'').trim().toLowerCase();let shown=0;cases.forEach(card=>{{const visible=matches(card,filter)&&(!q||card.dataset.search.includes(q));card.hidden=!visible;if(visible)shown++}});buttons.forEach(b=>{{const selected=b.dataset.filter===filter;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected))}});const label=buttons.find(b=>b.dataset.filter===filter&&!b.classList.contains('metric'));count.textContent=shown?`${{label?label.textContent.trim():filter}}: ${{shown}} case${{shown===1?'':'s'}} shown below`:'No cases match. Clear the search or choose All cases.'}}
function openCase(card){{remember();search.value='';apply('all');card.open=true;const summary=card.querySelector('summary');summary.focus({{preventScroll:true}});card.scrollIntoView({{behavior:motion,block:'start'}})}}
back.addEventListener('click',()=>{{const saved=views.pop();if(!saved)return;search.value=saved.query;cases.forEach(c=>c.open=saved.open.includes(c.id));apply(saved.filter);back.hidden=views.length===0;window.scrollTo({{top:saved.y,behavior:'instant'}})}});
cases.forEach(card=>card.querySelector('summary').addEventListener('click',()=>{{if(!card.open)remember()}}));
document.querySelectorAll('.openlink').forEach(link=>link.addEventListener('click',event=>{{const card=document.getElementById(link.getAttribute('href').slice(1));if(card){{event.preventDefault();openCase(card)}}}}));
buttons.forEach(button=>button.addEventListener('click',()=>{{if(current!==button.dataset.filter)remember();cases.forEach(c=>c.open=false);apply(button.dataset.filter);revealResults()}}));search.addEventListener('input',()=>apply());document.querySelector('#tour').addEventListener('click',()=>{{remember();search.value='';apply('risk');if(!cases.some(c=>!c.hidden))apply('actionable');const first=cases.find(c=>!c.hidden);if(first){{first.open=true;first.scrollIntoView({{behavior:motion,block:'start'}})}}}});count.setAttribute('role','status');count.setAttribute('aria-live','polite');apply();
</script></html>"""


DEMO = Path(__file__).with_name("demo.json")


def handler_factory(model, mailbox_context=None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            if self.path == "/api/report" and mailbox_context:
                try:
                    mailbox_context.sync()
                    return self._send(200, json.dumps(mailbox_context.report(), ensure_ascii=False), "application/json; charset=utf-8")
                except Exception as exc:
                    return self._send(500, json.dumps({"error": str(exc)}), "application/json; charset=utf-8")
            if self.path != "/": return self._send(404, "Not found", "text/plain")
            if mailbox_context:
                mailbox_context.sync()
                return self._send(200, render_html(mailbox_context.report(), interactive=True))
            demo = DEMO.read_text()
            body = "<!doctype html><meta charset='utf-8'><title>PactSignal local analyzer</title><style>body{max-width:960px;margin:35px auto;font:16px system-ui}textarea{width:100%;height:420px}button{padding:12px 20px;background:#0a786b;color:white;border:0;border-radius:8px;font-weight:bold}</style><h1>PactSignal local analyzer</h1><p>Paste a JSON array. Processing stays in this local Python process.</p><form method='post' action='/analyze'><textarea name='payload'>"+html.escape(demo)+"</textarea><p><button>Analyze locally</button></p></form>"
            self._send(200, body)
        def do_POST(self):
            if self.path == "/confirm" and mailbox_context:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 20_000: raise ValueError("confirmation request is too large")
                    form = parse_qs(self.rfile.read(length).decode())
                    message_id = form.get("message_id", [""])[0]
                    mailbox_context.store.confirm_received(message_id)
                    return self._send(200, render_html(mailbox_context.report(), interactive=True))
                except ValueError as exc:
                    return self._send(400, "Confirmation error: " + html.escape(str(exc)), "text/plain; charset=utf-8")
            if self.path != "/analyze": return self._send(404, "Not found", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2_000_000: raise ValueError("request exceeds 2MB")
                form = parse_qs(self.rfile.read(length).decode())
                report = analyze(json.loads(form.get("payload", [""])[0]), model)
                self._send(200, render_html(report))
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, "Input error: " + html.escape(str(exc)), "text/plain; charset=utf-8")
        def log_message(self, fmt, *args): pass
    return Handler


class LiveMailboxContext:
    """Thread-safe read-only mailbox synchronisation used by the local UI."""
    def __init__(self, source: Path, database: Path, model: IntentModel, settlements_csv: Path | None = None):
        self.source, self.store, self.model = source, MailboxStore(database), model
        self.settlements_csv = settlements_csv
        self.lock = threading.Lock()
        self.last_sync = None

    def sync(self):
        with self.lock:
            self.last_sync = sync_source(self.source, self.store)
            return self.last_sync

    def report(self):
        messages = self.store.messages()
        if not messages:
            raise ValueError("mail source contains no readable messages")
        settlement_provenance = None
        if self.settlements_csv:
            settlements, settlement_provenance = read_settlements_csv(self.settlements_csv)
            messages += settlements
        report = analyze(messages, self.model)
        if self.last_sync:
            report["mailbox_sync"] = asdict(self.last_sync)
        if settlement_provenance:
            report["settlement_import"] = settlement_provenance
        return report

    def start_background_sync(self, seconds: int):
        if seconds < 5:
            raise ValueError("mail polling interval must be at least 5 seconds")
        def worker():
            while True:
                try:
                    self.sync()
                except Exception:
                    pass
                time.sleep(seconds)
        thread = threading.Thread(target=worker, name="pactsignal-mail-sync", daemon=True)
        thread.start()
        return thread


class LiveGmailContext(LiveMailboxContext):
    """Read-only Gmail OAuth context; created only on explicit CLI request."""
    def __init__(self, client_file: Path, token_file: Path, query: str, database: Path, model: IntentModel, settlements_csv: Path | None = None):
        from gmail_connector import GmailReadOnlyConnector, build_readonly_service
        self.source = "gmail-readonly"
        self.store, self.model = MailboxStore(database), model
        self.lock = threading.Lock()
        self.last_sync = None
        self.settlements_csv = settlements_csv
        service = build_readonly_service(client_file, token_file)
        self.connector = GmailReadOnlyConnector(service, query=query)

    def sync(self):
        from gmail_connector import sync_gmail
        with self.lock:
            self.last_sync = sync_gmail(self.connector, self.store)
            return self.last_sync


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--format", choices=("json", "html"), default="json")
    p.add_argument("--evaluate", action="store_true")
    p.add_argument("--verify-report", type=Path)
    p.add_argument("--serve", action="store_true")
    p.add_argument("--public-showcase", action="store_true", help="serve only bundled fictional studio data on 0.0.0.0; no personal connectors or writes")
    p.add_argument("--mail-source", type=Path, help="read-only .eml directory, Maildir, mbox, or .eml file")
    p.add_argument("--database", type=Path, default=Path("pactsignal-local.sqlite3"))
    p.add_argument("--sync-mail", action="store_true", help="sync --mail-source once and print the analysed report")
    p.add_argument("--confirm-message", help="locally mark one imported payment notice as independently received")
    p.add_argument("--poll-seconds", type=int, default=30, help="background mailbox refresh interval")
    p.add_argument("--sync-gmail", action="store_true", help="explicitly run the optional read-only Gmail OAuth connector")
    p.add_argument("--gmail-client", type=Path, help="Google OAuth desktop-client JSON; never a mailbox password")
    p.add_argument("--gmail-token", type=Path, default=Path("pactsignal-gmail-token.json"))
    p.add_argument("--gmail-query", default="newer_than:90d", help="Gmail search query for the read-only connector")
    p.add_argument("--settlements-csv", type=Path, help="optional read-only normalized bank/provider CSV export")
    p.add_argument("--export-case", help="write a portable JSON evidence packet for one thread id")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    if args.public_showcase:
        if not args.serve or any((args.mail_source, args.gmail_client, args.input, args.output, args.settlements_csv, args.confirm_message, args.sync_mail, args.sync_gmail)):
            p.error("--public-showcase requires --serve and forbids personal inputs/connectors")
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            context = LiveMailboxContext(Path(__file__).with_name("studio-demo-mailbox"), Path(folder) / "fictional.sqlite3", IntentModel(), Path(__file__).with_name("studio-demo-settlements.csv"))
            context.sync()
            page = render_html(context.report(), interactive=False, showcase=True)
            context.store.close()
        class ShowcaseHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/":
                    self.send_error(404); return
                data = page.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers(); self.wfile.write(data)
            def do_POST(self):
                self.send_error(405, "Fictional showcase is read-only")
            def log_message(self, *args): pass
        ThreadingHTTPServer(("0.0.0.0", args.port), ShowcaseHandler).serve_forever()
        return
    model = IntentModel()
    if args.confirm_message:
        store = MailboxStore(args.database)
        try:
            store.confirm_received(args.confirm_message)
            payload = store.messages()
            if args.settlements_csv:
                settlements, provenance = read_settlements_csv(args.settlements_csv)
                payload += settlements
            result = analyze(payload, model)
            if args.settlements_csv:
                result["settlement_import"] = provenance
        finally:
            store.close()
        if args.export_case:
            if args.format == "html": p.error("--export-case supports JSON output only")
            result = case_evidence_packet(result, args.export_case)
        text = render_html(result) if args.format == "html" else json.dumps(result, indent=2, ensure_ascii=False)
        if args.output: args.output.write_text(text, encoding="utf-8")
        else: print(text)
        return
    if args.sync_gmail:
        if not args.gmail_client: p.error("--sync-gmail requires --gmail-client")
        context = LiveGmailContext(args.gmail_client, args.gmail_token, args.gmail_query, args.database, model)
        sync = context.sync()
        result = context.report()
        result["mailbox_sync"] = asdict(sync)
        if args.export_case:
            if args.format == "html": p.error("--export-case supports JSON output only")
            result = case_evidence_packet(result, args.export_case)
        text = render_html(result) if args.format == "html" else json.dumps(result, indent=2, ensure_ascii=False)
        if args.output: args.output.write_text(text, encoding="utf-8")
        else: print(text)
        return
    if args.sync_mail:
        if not args.mail_source: p.error("--sync-mail requires --mail-source")
        store = MailboxStore(args.database)
        try:
            sync = sync_source(args.mail_source, store)
            payload = store.messages()
            if args.settlements_csv:
                settlements, provenance = read_settlements_csv(args.settlements_csv)
                payload += settlements
            result = analyze(payload, model)
            if args.settlements_csv:
                result["settlement_import"] = provenance
            result["mailbox_sync"] = asdict(sync)
        finally:
            store.close()
        if args.export_case:
            if args.format == "html": p.error("--export-case supports JSON output only")
            result = case_evidence_packet(result, args.export_case)
        text = render_html(result) if args.format == "html" else json.dumps(result, indent=2, ensure_ascii=False)
        if args.output: args.output.write_text(text, encoding="utf-8")
        else: print(text)
        return
    if args.serve:
        if args.input or args.output or args.evaluate or args.verify_report: p.error("--serve cannot be combined with file or evaluation options")
        if args.mail_source and args.gmail_client:
            p.error("choose one live source: --mail-source or --gmail-client")
        mailbox_context = (
            LiveMailboxContext(args.mail_source, args.database, model, args.settlements_csv) if args.mail_source else
            LiveGmailContext(args.gmail_client, args.gmail_token, args.gmail_query, args.database, model, args.settlements_csv) if args.gmail_client else
            None
        )
        if mailbox_context:
            mailbox_context.start_background_sync(args.poll_seconds)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_factory(model, mailbox_context))
        print(f"PactSignal available only on http://127.0.0.1:{args.port}")
        server.serve_forever(); return
    if args.verify_report:
        if args.input or args.evaluate: p.error("--verify-report cannot be combined with input or evaluation options")
        result = verify_report_integrity(json.loads(args.verify_report.read_text()))
    else:
        if args.evaluate:
            result = evaluate(model)
        else:
            payload = json.loads((args.input or DEMO).read_text())
            if args.settlements_csv:
                settlements, provenance = read_settlements_csv(args.settlements_csv)
                payload += settlements
            result = analyze(payload, model)
            if args.settlements_csv:
                result["settlement_import"] = provenance
    if args.export_case:
        if args.evaluate or args.verify_report or args.format == "html":
            p.error("--export-case requires an analysed JSON report source and JSON output")
        result = case_evidence_packet(result, args.export_case)
    text = render_html(result) if args.format == "html" and not args.evaluate else json.dumps(result, indent=2, ensure_ascii=False)
    if args.output: args.output.write_text(text, encoding="utf-8")
    else: print(text)


if __name__ == "__main__": main()
