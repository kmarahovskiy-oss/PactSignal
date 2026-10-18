"""Local-first mailbox connectors for PactSignal.

The default connector reads RFC 822 messages from a Maildir, an mbox file, or
a directory containing .eml files.  No connector sends mail.  Raw message
bodies remain in a local SQLite database selected by the user.

Gmail and Outlook should use their official OAuth APIs in production.  The
filesystem connector is intentionally provider-neutral: it lets the complete
sync/analyse/dashboard loop be exercised without credentials or a cloud
account, and it is also useful with a continuously mirrored mailbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
import json
import mailbox
from pathlib import Path
import re
import sqlite3
import threading
from typing import Iterable


REPLY_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", re.I)
QUOTED_REPLY = re.compile(
    r"\n(?:On .+? wrote:|From:\s.+?\nSent:\s.+?\n|[- ]{2,}Original Message[- ]{2,}).*$",
    re.I | re.S,
)


@dataclass(frozen=True)
class SyncResult:
    source: str
    scanned: int
    imported: int
    unchanged: int
    errors: tuple[str, ...]


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except (LookupError, UnicodeError):
        return value.strip()


def _plain_body(message) -> str:
    if message.is_multipart():
        candidates = []
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            if part.get_content_type() == "text/plain":
                try:
                    candidates.append(part.get_content())
                except (LookupError, UnicodeError):
                    continue
        body = "\n".join(x.strip() for x in candidates if x.strip())
    else:
        try:
            body = message.get_content()
        except (LookupError, UnicodeError):
            payload = message.get_payload(decode=True) or b""
            body = payload.decode("utf-8", errors="replace")
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return QUOTED_REPLY.sub("", body).strip()


def _iso_date(value: str | None) -> str:
    try:
        dt = parsedate_to_datetime(value or "")
    except (TypeError, ValueError, OverflowError):
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _address(value: str | None) -> str:
    addresses = getaddresses([value or ""])
    if not addresses:
        return _decode(value)
    name, address = addresses[0]
    return f"{_decode(name)} <{address}>" if name else address


def _thread_key(message, subject: str) -> str:
    provider_thread = message.get("X-GM-THRID") or message.get("Thread-Index")
    if provider_thread:
        return "provider-" + hashlib.sha256(str(provider_thread).encode()).hexdigest()[:20]
    refs = (message.get("References") or message.get("In-Reply-To") or "").split()
    if refs:
        return "ref-" + hashlib.sha256(refs[0].encode()).hexdigest()[:20]
    participants = sorted(
        address.lower()
        for _, address in getaddresses([
            message.get("From", ""), message.get("To", ""), message.get("Cc", "")
        ])
        if address
    )
    normalized = REPLY_PREFIX.sub("", subject).strip().lower()
    seed = json.dumps([normalized, participants], ensure_ascii=False, separators=(",", ":"))
    return "topic-" + hashlib.sha256(seed.encode()).hexdigest()[:20]


def parse_rfc822(raw: bytes, source_key: str = "") -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw)
    subject = _decode(message.get("Subject"))
    body = _plain_body(message)
    message_id = (message.get("Message-ID") or "").strip(" <>")
    if not message_id:
        message_id = hashlib.sha256(raw).hexdigest()
    if not body:
        body = "[No readable text/plain body]"
    return {
        "id": message_id,
        "thread_id": _thread_key(message, subject),
        "timestamp": _iso_date(message.get("Date")),
        "sender": _address(message.get("From")),
        "subject": subject,
        "body": body[:20_000],
        "source_key": source_key,
    }


def iter_source(path: Path) -> Iterable[tuple[str, bytes]]:
    path = path.expanduser().resolve()
    if path.is_file() and path.suffix.lower() in {".mbox", ".mbx"}:
        box = mailbox.mbox(path, create=False)
        for index, message in enumerate(box):
            yield f"{path}#{index}", message.as_bytes(policy=policy.default)
        return
    if path.is_dir() and all((path / name).exists() for name in ("cur", "new", "tmp")):
        box = mailbox.Maildir(path, create=False)
        for key, message in box.iteritems():
            yield f"{path}#{key}", message.as_bytes(policy=policy.default)
        return
    if path.is_dir():
        for item in sorted(path.rglob("*.eml")):
            yield str(item), item.read_bytes()
        return
    if path.is_file() and path.suffix.lower() == ".eml":
        yield str(path), path.read_bytes()
        return
    raise ValueError("mail source must be an .eml file, .eml directory, Maildir, or mbox file")


class MailboxStore:
    """Small local persistence layer with idempotent incremental imports."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ThreadingHTTPServer handles each request on a worker thread while the
        # background importer has its own thread.  SQLite permits this when the
        # application serialises access explicitly.
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS messages(
                id TEXT PRIMARY KEY,
                source_key TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )"""
        )
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS confirmations(
                message_id TEXT PRIMARY KEY,
                confirmation TEXT NOT NULL,
                confirmed_at TEXT NOT NULL
            )"""
        )
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def upsert(self, message: dict, raw: bytes) -> bool:
        with self.lock:
            digest = hashlib.sha256(raw).hexdigest()
            existing = self.db.execute(
                "SELECT content_sha256 FROM messages WHERE id = ?", (message["id"],)
            ).fetchone()
            if existing and existing[0] == digest:
                return False
            payload = {k: v for k, v in message.items() if k != "source_key"}
            now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            self.db.execute(
                """INSERT INTO messages(id,source_key,content_sha256,payload_json,imported_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   source_key=excluded.source_key,content_sha256=excluded.content_sha256,
                   payload_json=excluded.payload_json,imported_at=excluded.imported_at""",
                (message["id"], message["source_key"], digest, json.dumps(payload, ensure_ascii=False), now),
            )
            self.db.commit()
            return True

    def messages(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT payload_json FROM messages ORDER BY imported_at,id").fetchall()
            result = [json.loads(row[0]) for row in rows]
            confirmations = dict(self.db.execute("SELECT message_id,confirmation FROM confirmations"))
        for message in result:
            if message["id"] in confirmations:
                message["confirmation"] = confirmations[message["id"]]
        return result

    def confirm_received(self, message_id: str, source: str = "user_confirmed_received"):
        if source not in {"user_confirmed_received", "provider_notice_authenticated"}:
            raise ValueError("unsupported confirmation source")
        with self.lock:
            if not self.db.execute("SELECT 1 FROM messages WHERE id = ?", (message_id,)).fetchone():
                raise ValueError("message is not present in the local mailbox")
            now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            self.db.execute(
                """INSERT INTO confirmations(message_id,confirmation,confirmed_at) VALUES(?,?,?)
                   ON CONFLICT(message_id) DO UPDATE SET confirmation=excluded.confirmation,
                   confirmed_at=excluded.confirmed_at""",
                (message_id, source, now),
            )
            self.db.commit()

    def count(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])


def sync_source(source: Path, store: MailboxStore) -> SyncResult:
    scanned = imported = 0
    errors = []
    for key, raw in iter_source(source):
        scanned += 1
        try:
            parsed = parse_rfc822(raw, key)
            imported += int(store.upsert(parsed, raw))
        except Exception as exc:  # isolate one malformed message
            errors.append(f"{key}: {type(exc).__name__}: {exc}")
    return SyncResult(str(Path(source).expanduser().resolve()), scanned, imported, scanned - imported, tuple(errors))
