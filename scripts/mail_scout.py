"""Read-only Gmail IMAP scout for daily job application and response tracking."""

import argparse
import email
import imaplib
import json
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

JOB_KEYWORDS = (
    "application", "applied", "applicant", "candidate", "position", "role",
    "interview", "assessment", "coding challenge", "case study", "offer",
    "unfortunately", "regret", "not moving forward", "other candidates",
    "başvuru", "başvurunuz", "mülakat", "görüşme", "değerlendirme",
    "teklif", "olumsuz", "teşekkür", "kariyer", "pozisyon", "aday",
    "career", "recruit", "talent", "hiring", "hr",
)

ATS_DOMAINS = (
    "linkedin.com", "kariyer.net", "greenhouse.io", "lever.co", "workday.com",
    "myworkdayjobs.com", "ashbyhq.com", "smartrecruiters.com", "bamboohr.com",
    "recruitee.com", "teamtailor.com", "join.com", "indeed.com", "glassdoor.com",
    "hrpeak.com", "kolayik.com", "peoplise.com",
)

FREE_DOMAINS = {
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "proton.me", "protonmail.com", "yandex.com", "live.com",
}


def load_env(path: Path = ENV_PATH) -> Dict[str, str]:
    """Load simple KEY=VALUE pairs from .env without external dependencies."""
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _safe_decode(payload: bytes, charset: Optional[str]) -> str:
    enc = (charset or "utf-8").strip()
    try:
        return payload.decode(enc, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _decode_str(val: Optional[str]) -> str:
    if not val:
        return ""
    parts = []
    for chunk, enc in decode_header(val):
        if isinstance(chunk, bytes):
            parts.append(_safe_decode(chunk, enc))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _extract_body_snippet(msg: Message, max_chars: int = 600) -> str:
    """Extract plain text snippet (falling back to stripped HTML)."""
    text_part = ""
    html_part = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                continue
            ctype = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = _safe_decode(payload, part.get_content_charset())
            if ctype == "text/plain" and not text_part:
                text_part = decoded
            elif ctype == "text/html" and not html_part:
                html_part = decoded
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = _safe_decode(payload, msg.get_content_charset())
            if msg.get_content_type() == "text/html":
                html_part = decoded
            else:
                text_part = decoded

    raw = text_part or re.sub(r"<[^>]+>", " ", html_part)
    cleaned = re.sub(r"\s+", " ", raw).strip()
    return cleaned[:max_chars]


def _classify_hint(subject: str, snippet: str) -> str:
    blob = f"{subject} {snippet}".lower()
    if any(w in blob for w in ("unfortunately", "regret", "not moving forward", "other candidates", "olumsuz", "ne yazık ki")):
        return "rejection"
    if any(w in blob for w in ("interview", "mülakat", "görüşme", "schedule", "availability", "case study", "assessment", "offer", "teklif")):
        return "action_or_interview"
    if any(w in blob for w in ("thank you for applying", "application received", "we received your application", "başvurunuz alınmıştır", "başvurun ulaştı")):
        return "application_confirmation"
    return "job_update"


def _is_job_related(subject: str, sender_addr: str, snippet: str) -> bool:
    domain = sender_addr.split("@")[-1].lower() if "@" in sender_addr else ""
    if any(ats in domain for ats in ATS_DOMAINS):
        return True
    blob = f"{subject} {sender_addr} {snippet}".lower()
    return any(kw in blob for kw in JOB_KEYWORDS)


def _fetch_message(imap: imaplib.IMAP4_SSL, msg_id: bytes, include_body: bool = True) -> Dict[str, Any]:
    fetch_spec = "(RFC822)" if include_body else "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID IN-REPLY-TO)])"
    status, data = imap.fetch(msg_id, fetch_spec)
    if status != "OK" or not data or not isinstance(data[0], tuple):
        return {}
    msg = email.message_from_bytes(data[0][1])
    subject = _decode_str(msg.get("Subject"))
    from_raw = _decode_str(msg.get("From"))
    from_name, from_addr = parseaddr(from_raw)
    date_raw = msg.get("Date", "")
    try:
        dt_iso = parsedate_to_datetime(date_raw).isoformat()
    except Exception:
        dt_iso = date_raw
    snippet = _extract_body_snippet(msg) if include_body else ""
    return {
        "uid": msg_id.decode(errors="ignore"),
        "date": dt_iso,
        "from_name": from_name,
        "from_email": from_addr,
        "subject": subject,
        "message_id": (msg.get("Message-ID") or "").strip(),
        "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
        "category_hint": _classify_hint(subject, snippet),
        "snippet": snippet,
    }


def _find_initial_application(
    imap: imaplib.IMAP4_SSL,
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """If an email is a response/update, search inbox for the earlier initial application mail."""
    from_email = item.get("from_email", "")
    domain = from_email.split("@")[-1].lower() if "@" in from_email else ""
    in_reply_to = item.get("in_reply_to", "")
    current_uid = item.get("uid")

    search_queries: List[str] = []
    if in_reply_to:
        search_queries.append(f'(HEADER Message-ID "{in_reply_to}")')
    if domain and domain not in FREE_DOMAINS and not any(ats in domain for ats in ATS_DOMAINS):
        search_queries.append(f'(FROM "{domain}")')
    elif from_email:
        search_queries.append(f'(FROM "{from_email}")')

    for query in search_queries:
        try:
            status, ids = imap.search(None, query)
        except Exception:
            continue
        if status != "OK" or not ids or not ids[0]:
            continue
        for mid in ids[0].split()[:10]:
            if mid.decode(errors="ignore") == current_uid:
                continue
            cand = _fetch_message(imap, mid, include_body=False)
            if cand and cand.get("date", "") <= item.get("date", ""):
                return {
                    "date": cand["date"],
                    "from": cand["from_email"],
                    "subject": cand["subject"],
                }
    return None


def scan_mail(days: int = 1, search_term: Optional[str] = None) -> Dict[str, Any]:
    env = load_env()
    addr = env.get("GMAIL_ADDRESS", "")
    pwd = env.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not addr or not pwd:
        return {
            "ok": False,
            "error": f"Missing GMAIL_ADDRESS or GMAIL_APP_PASSWORD in {ENV_PATH}",
        }

    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        imap.login(addr, pwd)
        imap.select("INBOX", readonly=True)

        if search_term:
            status, data = imap.search(None, f'(TEXT "{search_term}")')
        else:
            since_str = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
            status, data = imap.search(None, f'(SINCE "{since_str}")')

        if status != "OK":
            return {"ok": False, "error": f"IMAP search failed: {status}"}

        msg_ids = data[0].split() if data and data[0] else []
        results: List[Dict[str, Any]] = []
        for mid in reversed(msg_ids[-150:]):
            item = _fetch_message(imap, mid, include_body=True)
            if not item:
                continue
            if search_term or _is_job_related(item["subject"], item["from_email"], item["snippet"]):
                if item["category_hint"] in ("rejection", "action_or_interview", "job_update"):
                    initial = _find_initial_application(imap, item)
                    if initial:
                        item["initial_application"] = initial
                results.append(item)

        return {
            "ok": True,
            "scanned_count": len(msg_ids),
            "matched_count": len(results),
            "emails": results,
        }
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Gmail IMAP job scout")
    parser.add_argument("--days", type=int, default=1, help="Days back to scan (default: 1)")
    parser.add_argument("--search", type=str, default=None, help="Optional ad-hoc text search")
    args = parser.parse_args()
    print(json.dumps(scan_mail(days=args.days, search_term=args.search), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
