"""Read-only import of normalized bank/payment-provider CSV exports.

PactSignal never connects to a financial account and never initiates a
transaction.  The adapter consumes a local export with an explicit schema and
turns settled rows into authenticated settlement evidence for reconciliation.
"""
from __future__ import annotations

import csv
from datetime import datetime
import hashlib
from pathlib import Path
import re


REQUIRED_COLUMNS = {
    "id", "timestamp", "payer", "amount", "currency", "reference", "status"
}
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "UAH", "USDC"}
SETTLED_STATUSES = {"settled", "completed", "credited"}


def _clean(value: str, limit: int = 300) -> str:
    return re.sub(r"[\r\n\t]+", " ", (value or "").strip())[:limit]


def read_settlements_csv(path: Path) -> tuple[list[dict], dict]:
    """Return PactSignal messages and a provenance summary for one local CSV."""
    raw = path.read_bytes()
    if len(raw) > 5_000_000:
        raise ValueError("settlement CSV exceeds 5 MB")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    columns = set(reader.fieldnames or [])
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError("settlement CSV missing columns: " + ", ".join(sorted(missing)))

    messages, seen, ignored = [], set(), 0
    for line_number, row in enumerate(reader, start=2):
        identifier = _clean(row["id"], 160)
        if not identifier:
            raise ValueError(f"settlement row {line_number} has no id")
        if identifier in seen:
            raise ValueError(f"duplicate settlement id: {identifier}")
        seen.add(identifier)
        status = _clean(row["status"], 30).lower()
        if status not in SETTLED_STATUSES:
            ignored += 1
            continue
        timestamp = _clean(row["timestamp"], 80)
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"settlement row {line_number} has invalid timestamp") from exc
        currency = _clean(row["currency"], 8).upper()
        if currency not in SUPPORTED_CURRENCIES:
            raise ValueError(f"settlement row {line_number} has unsupported currency")
        try:
            amount = float(_clean(row["amount"], 40).replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"settlement row {line_number} has invalid amount") from exc
        if not (0 < amount < 1_000_000_000):
            raise ValueError(f"settlement row {line_number} has invalid amount")
        payer = _clean(row["payer"], 200)
        if not payer:
            raise ValueError(f"settlement row {line_number} has no payer")
        reference = _clean(row["reference"], 300)
        messages.append({
            "id": f"settlement:{identifier}",
            "thread_id": f"settlement-source:{identifier}",
            "timestamp": timestamp,
            "sender": "Bank statement import <read-only@bank-statement.local>",
            "subject": f"Settled {amount:.2f} {currency} from {payer}",
            "body": (
                f"Settlement completed: {amount:.2f} {currency} credited from {payer}. "
                f"Reference: {reference or 'not supplied'}."
            ),
            "confirmation": "provider_notice_authenticated",
        })
    return messages, {
        "source": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "rows_imported": len(messages),
        "rows_ignored_not_settled": ignored,
        "schema": "pactsignal-normalized-settlement-csv-v1",
        "read_only": True,
    }
