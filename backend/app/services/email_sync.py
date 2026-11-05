"""Sync job-related emails (IMAP) into the applications table."""
from __future__ import annotations

import email
import hashlib
import imaplib
import json
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Callable

from ..config import get_settings
from .errors import SyncError

PRIORITY = ["offer", "rejected", "interview", "applied"]
RANK = {"wishlist": 0, "applied": 1, "interview": 2, "offer": 3}
ATS_DOMAINS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "myworkdayjobs.com", "workday.com", "icims.com",
               "smartrecruiters.com", "ashbyhq.com", "indeed.com", "linkedin.com")
NOISE_WORDS = re.compile(r"\b(recruiting|recruitment|careers|talent|jobs|hiring|team|hr|no-?reply|notifications?|"
                         r"acquisition|people)\b", re.I)
CAP = r"[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*"
COMPANY_PATTERNS = [
    re.compile(rf"\b(?:at|with|applying to|application to)\s+({CAP})"),
    re.compile(rf"(?:^|\W)({CAP})\s+application"),
]
ROLE_PATTERNS = [
    re.compile(r"for the (.+?) (?:position|role)\b", re.I),
    re.compile(r"application for (?:the |a )?(.+?)(?: position| role| at | with |\s*[-–|:,!.(]|$)", re.I),
]
FETCH_BATCH = 50
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]



def _decode(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _body_text(msg: Message) -> str:
    """Plain-text body; falls back to crudely stripped HTML."""
    plain, html = [], []
    for part in msg.walk():
        if part.is_multipart() or part.get_filename():
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if ctype == "text/plain" else html).append(text)
    if plain:
        return "\n".join(plain)
    stripped = re.sub(r"(?is)<(script|style).*?</\1>", " ", "\n".join(html))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped))


def classify(text: str, rules: dict[str, list[str]]) -> str | None:
    """Return the highest-priority status whose keywords appear in text."""
    low = text.lower()
    for status in PRIORITY:
        if any(k.lower() in low for k in rules.get(status, [])):
            return status
    return None


def extract_company(sender: str, subject: str) -> str:
    name, addr = parseaddr(sender)
    domain = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    is_ats = any(domain == d or domain.endswith("." + d) for d in ATS_DOMAINS)
    if is_ats:
        for pat in COMPANY_PATTERNS:
            m = pat.search(subject)
            if m:
                return re.sub(r"^Your\s+", "", m.group(1)).strip()
    cleaned = NOISE_WORDS.sub("", _decode(name)).strip(" -|,.")
    if cleaned and not (is_ats and cleaned.lower() in {d.split(".")[0] for d in ATS_DOMAINS}):
        return cleaned
    if is_ats or not domain:
        return ""
    labels = domain.split(".")
    return labels[-2 if len(labels) > 1 else 0].capitalize()


def extract_role(subject: str) -> str:
    for pat in ROLE_PATTERNS:
        m = pat.search(subject)
        if m:
            return m.group(1).strip(" \"'")
    return ""


def _external_id(company: str, role: str) -> str:
    key = f"{' '.join(company.lower().split())}|{role.lower().strip()}"
    return "email:" + hashlib.sha1(key.encode()).hexdigest()


def next_status(current: str, new: str) -> str:
    """Status only moves forward; rejected overrides applied/interview; withdrawn never changes."""
    if current in ("withdrawn", "rejected", "offer"):
        return current
    if new == "rejected":
        return "rejected"
    return new if RANK[new] > RANK[current] else current


def _imap_date(d: date) -> str:
    return f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}"


def _fetch_messages(imap, folder: str, since: date) -> list[Message]:
    typ, _ = imap.select(folder, readonly=True)
    if typ != "OK":
        raise SyncError(f"Could not open mail folder '{folder}'")
    typ, data = imap.search(None, "SINCE", _imap_date(since))
    if typ != "OK":
        raise SyncError("IMAP search failed")
    nums = (data[0] or b"").split()
    msgs = []
    for i in range(0, len(nums), FETCH_BATCH):  # one round trip per batch, not per message
        typ, parts = imap.fetch(b",".join(nums[i:i + FETCH_BATCH]), "(BODY.PEEK[]<0.60000>)")
        if typ != "OK":
            continue
        msgs.extend(email.message_from_bytes(p[1]) for p in parts if isinstance(p, tuple) and p[1])
    return msgs


def _msg_date(msg: Message, today: date) -> date:
    try:
        return parsedate_to_datetime(msg["Date"]).date()
    except Exception:
        return today


def sync_email(conn, imap_factory: Callable[[], object] | None = None, today: date | None = None, full: bool = False) -> dict:
    """Scan the mailbox and upsert applications; returns {added, updated, message}.

    The first sync (or full=True) looks back `initial_lookback_days`; later ones `lookback_days`."""
    s = get_settings()
    if imap_factory is None:
        if not (s.imap_host and s.imap_user and s.imap_password):
            raise SyncError("Email sync not configured: set IMAP_HOST, IMAP_USER and IMAP_PASSWORD")
        imap_factory = lambda: imaplib.IMAP4_SSL(s.imap_host)  # noqa: E731
    today = today or date.today()
    cfg = s.config["career"]["email"]
    first = full or conn.execute("SELECT 1 FROM settings WHERE key='last_sync_email'").fetchone() is None
    since = today - timedelta(days=int(cfg["initial_lookback_days" if first else "lookback_days"]))
    try:
        imap = imap_factory()
    except (OSError, imaplib.IMAP4.error) as e:
        raise SyncError(f"IMAP connection failed: {e}") from e
    try:
        try:
            imap.login(s.imap_user, s.imap_password)
            msgs = _fetch_messages(imap, cfg["folder"], since)
        except (OSError, imaplib.IMAP4.error) as e:
            raise SyncError(f"IMAP error: {e}") from e
    finally:
        try:
            imap.logout()
        except Exception:
            pass

    added = updated = 0
    for msg in sorted(msgs, key=lambda m: _msg_date(m, today)):
        subject = _decode(msg["Subject"])
        status = classify(subject, cfg["rules"]) or classify(_body_text(msg), cfg["rules"])
        if not status:
            continue
        company = extract_company(msg["From"] or "", subject)
        if not company:
            continue
        role = extract_role(subject)
        ext, when = _external_id(company, role), _msg_date(msg, today).isoformat()
        row = conn.execute("SELECT id, status FROM applications WHERE external_id=?", (ext,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO applications(company, role, status, applied_on, source, last_update, external_id) "
                         "VALUES (?, ?, ?, ?, 'email', ?, ?)", (company, role, status, when, when, ext))
            added += 1
        else:
            new = next_status(row["status"], status)
            if new != row["status"]:
                conn.execute("UPDATE applications SET status=?, last_update=? WHERE id=?", (new, when, row["id"]))
                updated += 1
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('last_sync_email', ?)",
                 (json.dumps(datetime.now(timezone.utc).isoformat()),))
    return {"added": added, "updated": updated, "message": f"Email: {added} added, {updated} updated"}
