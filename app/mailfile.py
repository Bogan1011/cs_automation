"""Read an uploaded email file: Outlook .msg, .eml, or plain .txt/.html. Returns subject, text body and date.

.msg files are OLE compound files; the subject and body are fixed MAPI property streams, so olefile (pure Python)
is enough and no compiler-heavy package is needed.
"""
import email
import email.policy
import email.utils
import io
import re
import struct
from datetime import datetime, timedelta, timezone

from .articles import parse as parse_html

MAX_BYTES = 25 * 1024 * 1024


class MailFileError(ValueError):
    pass


def html_to_text(html: str) -> str:
    return "\n\n".join(parse_html(html).lines)


def _msg_stream(ole, prop: str) -> str | None:
    """Read a string property: 001F is UTF-16, 001E is 8-bit, 0102 is binary (used for HTML)."""
    for suffix, codec in (("001F", "utf-16-le"), ("001E", "cp1252"), ("0102", None)):
        name = f"__substg1.0_{prop}{suffix}"
        if ole.exists(name):
            data = ole.openstream(name).read()
            if codec:
                return data.decode(codec, errors="replace").rstrip("\x00")
            for enc in ("utf-8", "cp1252"):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
    return None


def _msg_date(ole) -> datetime | None:
    """PR_MESSAGE_DELIVERY_TIME (0E06) or PR_CLIENT_SUBMIT_TIME (0039) from the top-level property stream."""
    if not ole.exists("__properties_version1.0"):
        return None
    data = ole.openstream("__properties_version1.0").read()
    found = {}
    for off in range(32, len(data) - 15, 16):  # 32-byte header for the top-level message, then 16-byte entries
        ptype, pid = struct.unpack_from("<HH", data, off)
        if ptype == 0x0040 and pid in (0x0E06, 0x0039):
            found[pid] = struct.unpack_from("<Q", data, off + 8)[0]
    ft = found.get(0x0E06) or found.get(0x0039)
    if not ft:
        return None
    return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=ft // 10)


def read_msg(data: bytes) -> dict:
    import olefile
    if not olefile.isOleFile(io.BytesIO(data)):
        raise MailFileError("This .msg file isn't a valid Outlook message.")
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        subject = _msg_stream(ole, "0037") or ""
        body = _msg_stream(ole, "1000") or ""
        if len(body.strip()) < 20:
            html = _msg_stream(ole, "1013")
            if html:
                body = html_to_text(html)
        sender = _msg_stream(ole, "0C1A") or ""
        return {"subject": subject.strip(), "body": body, "date": _msg_date(ole), "sender": sender.strip()}
    finally:
        ole.close()


def read_eml(data: bytes) -> dict:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = ""
    if part is not None:
        body = part.get_content()
        if part.get_content_type() == "text/html":
            body = html_to_text(body)
    date = None
    try:
        date = email.utils.parsedate_to_datetime(msg["date"]) if msg["date"] else None
    except (TypeError, ValueError):
        pass
    return {"subject": str(msg["subject"] or "").strip(), "body": body, "date": date,
            "sender": str(msg["from"] or "")}


def read_upload(filename: str, data: bytes) -> dict:
    if not data:
        raise MailFileError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise MailFileError("The file is larger than 25 MB.")
    name = (filename or "").lower()
    if name.endswith(".msg") or data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        out = read_msg(data)
    elif name.endswith(".eml") or re.match(rb"^(Received|Return-Path|From|Subject|MIME-Version|Date|Message-ID):", data[:200], re.I):
        out = read_eml(data)
    elif name.endswith((".txt", ".htm", ".html")):
        text = data.decode("utf-8", errors="replace")
        out = {"subject": "", "body": html_to_text(text) if name.endswith(("htm", "html")) else text, "date": None, "sender": ""}
    else:
        raise MailFileError("Upload an Outlook .msg or .eml file (in Outlook: File > Save As, or drag the mail to a folder).")
    out["body"] = re.sub(r"\n{3,}", "\n\n", (out["body"] or "").replace("\r\n", "\n")).strip()
    return out
