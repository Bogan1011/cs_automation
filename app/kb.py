"""Turn a pasted case-closure email into a knowledge entry: case number, title, issue, root cause, solution.

Trend and our own closure mails usually have headings like "Issue:", "Problem Description", "Root Cause",
"Resolution" or "Solution". Text under each heading is kept until the next known heading or the signature.
Everything stays editable on the form, so a wrong guess is fixed before saving.
"""
import re

PRODUCTS = {
    "deep-security": "Deep Security",
    "xdr": "XDR / Vision One",
    "apex-one": "Apex One",
    "endpoint-encryption": "Endpoint Encryption",
    "other": "Other",
}

_HEADS = {
    "issue": ["issue description", "problem description", "issue summary", "issue details", "issue",
              "problem statement", "problem", "symptoms", "symptom", "concern", "query", "description"],
    "root_cause": ["root cause analysis", "root cause", "rca", "cause"],
    "solution": ["resolution steps", "resolution", "solution provided", "solution", "fix applied", "fix",
                 "workaround", "action taken", "actions taken", "steps taken", "recommendation"],
    "_stop": ["environment", "product version", "case summary", "next steps", "additional information", "note",
              "attachments"],
}
_ALL = sorted(((h, k) for k, hs in _HEADS.items() for h in hs), key=lambda x: -len(x[0]))
_WORDS = "|".join(re.escape(h) for h, _ in _ALL)
# "Issue: ...", "Root Cause (RCA) - ...", "**Resolution**", "1. Solution" and a heading alone on its line.
_HEAD_RE = re.compile(r"^[\s>*#\-•\d.)]*(" + _WORDS + r")\b\s*(?:\([^)]*\))?\**\s*(?:[:\-–]\s*(.*)|$)", re.I)
_SIGNATURE = re.compile(r"^\s*(thanks( and| &)? regards|best regards|kind regards|regards|thank you|thanks|"
                        r"warm regards|-{2,}\s*original message|from:\s|sent:\s|on .+ wrote:)\b", re.I)
_CASE_RE = re.compile(r"\b(TM-\d{6,10})\b|\bcase\s*(?:no\.?|number|id|#)?\s*[:#]?\s*(\d{6,10})\b", re.I)


def _classify(line: str) -> tuple[str, str] | None:
    m = _HEAD_RE.match(line)
    if not m:
        return None
    word = m.group(1).lower()
    key = next(k for h, k in _ALL if h == word)
    return key, (m.group(2) or "").strip()


def guess_product(text: str) -> str:
    t = text.lower()
    scores = {
        "deep-security": len(re.findall(r"deep security|workload|\bdsa\b|\bdsm\b|ds_agent|c1ws|cloud one|swp", t)),
        "apex-one": len(re.findall(r"apex one|apex central|officescan|ntrtscan|tmlisten|\bsep\b|standard endpoint|pccnt", t)),
        "xdr": len(re.findall(r"\bxdr\b|vision one|endpoint sensor|basecamp|endpoint inventory|workbench|sensor", t)),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def _clean(lines: list[str]) -> str:
    out = "\n".join(l.rstrip() for l in lines).strip()
    return re.sub(r"\n{3,}", "\n\n", out)


def extract(email: str, subject: str = "") -> dict:
    """Best-effort split of a closure email. Returns case_no, title, issue, root_cause, solution, product."""
    text = (email or "").replace("\r\n", "\n")
    lines = text.split("\n")
    first = next((l for l in lines if l.strip()), "")
    if not subject and re.match(r"^\s*(subject|re|fw|fwd)\s*:", first, re.I):
        subject = re.sub(r"^\s*subject\s*:\s*", "", first, flags=re.I)
    sections: dict[str, list[str]] = {}
    current = None
    for line in lines:
        if _SIGNATURE.match(line) and current:
            current = None
            if sections.get("solution"):  # the reply chain below the signature is older mail
                break
            continue
        hit = _classify(line)
        if hit:
            key, rest = hit
            current = None if key == "_stop" else key
            if current:
                sections.setdefault(current, [])
                if rest:
                    sections[current].append(rest)
            continue
        if current:
            sections[current].append(line)
    m = _CASE_RE.search(subject + "\n" + text)
    case_no = (m.group(1) or m.group(2)) if m else ""
    title = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", subject, flags=re.I)
    title = re.sub(r"\b(case\s*)?TM-\d+\b[:\s-]*|\bcase\s*(no\.?|#)?\s*\d{6,10}\b[:\s-]*", "", title, flags=re.I)
    title = re.sub(r"\b(created|closed|resolved|updated)\b\s*:?", "", title, flags=re.I).strip(" -:")
    issue = _clean(sections.get("issue", []))
    if not title:
        title = (issue.split("\n")[0] if issue else "")[:200]
    return {
        "case_no": case_no.upper(),
        "title": title[:300],
        "issue": issue,
        "root_cause": _clean(sections.get("root_cause", [])),
        "solution": _clean(sections.get("solution", [])),
        "product": guess_product(subject + " " + text),
    }


# ---------- whole-thread summary (Trend case mails rarely have "Issue:" / "Solution:" headings) ----------

_SPLIT = re.compile(r"^\s*(-{2,}\s*original message\s*-{2,}|_{8,}|from:\s.+|on .{6,80} wrote:)\s*$", re.I | re.M)
# Lines that are never part of the case content.
_JUNK = re.compile(
    r"^\s*(from|sent|to|cc|bcc|subject|date|received|importance)\s*:|^\s*-{2,}\s*original message|^\s*_{8,}\s*$|"
    r"^\s*on .{6,80} wrote:\s*$|"
    r"caution:|this (e-?mail|message) (originated|was sent) from outside|external (e-?mail|sender)|"
    r"system generated email|do not change the (e-?mail )?subject|"
    r"the information contained in this|intended (solely )?for the|disclaimer|confidential|"
    r"warning: computer viruses|accepts no liability|"
    r"^\s*<?(https?://|mailto:)\S+>?\s*$|^\s*\[cid:|^\s*<image", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|dear|good (morning|afternoon|evening))\b[^.\n]{0,60}[,!]?\s*$", re.I)
_PLEASANTRY = re.compile(r"^\s*(thank you for (your patience|contacting|reaching|the update|sharing)|thanks for|"
                         r"apologies for the delay|sorry for the delay|hope you are|i hope this)", re.I)
_SIGN_OFF = re.compile(r"^\s*(thanks|thank you|regards|best regards|warm regards|kind regards|thanks (and|&) regards|"
                       r"thank you and stay safe|cheers|sincerely)\b[^\n]{0,30}$", re.I)
_ACK = re.compile(r"system generated email|we are reviewing your submission|thank you for contacting|"
                  r"has been (created|logged|registered)|additional resources", re.I)
_SOLVED = re.compile(r"\b(resolv|fix|solution|workaround|work-around|recommend|upgrade|install|apply|"
                     r"(please|kindly) (update|upgrade|install|apply|uninstall|restart|reboot|enable|disable|add|exclude|run)|"
                     r"after (applying|upgrading|updating|installing|restarting|rebooting)|"
                     r"(issue|problem) (is|was|has been) (now )?(fixed|resolved|gone|solved)|hotfix|patch|steps?)\b", re.I)
_CAUSE = re.compile(r"\b(root cause|caused by|cause (is|was|seems)|(was|is|be) due to)\b", re.I)
_ASK = re.compile(r"\b(below is|here is|regarding|guidance on|as requested|you asked|your (query|question|request))\b", re.I)


def _clean_message(msg: str) -> tuple[str, dict]:
    """Body of one message without headers, banners, greeting, pleasantries, signature and disclaimers.
    Also returns case metadata lines (Case:, Product:) found in Trend's acknowledgement mails."""
    meta = {}
    out, started = [], False
    for raw in msg.split("\n"):
        line = raw.replace("\xa0", " ").rstrip()
        m = re.match(r"^\s*(case|product)\s*:\s*(.+)$", line, re.I)
        if m:
            meta[m.group(1).lower()] = m.group(2).strip()
            continue
        if _JUNK.search(line):
            continue
        if not started and (not line.strip() or _GREETING.match(line)):
            continue
        if not started:
            line = re.sub(r"^\s*(hi|hello|dear)\s+[\w ]{0,30}[,!]\s*", "", line, flags=re.I)
        if not started and _PLEASANTRY.match(line):  # drop the polite sentences, keep the rest of the line
            line = " ".join(x for x in re.split(r"(?<=[.!])\s+", line.strip()) if not _PLEASANTRY.match(x))
            if not line.strip():
                continue
        if _SIGN_OFF.match(line) and started:
            break  # name, title, address and social links follow
        started = True
        # "text: https://x <https://x>" -> "text: https://x" ; Outlook bullets "*<tab>" -> "• "
        line = re.sub(r"(https?://\S+)\s*<\1>", r"\1", line)
        line = re.sub(r"\s*<(https?://|mailto:)[^>]+>", "", line)
        line = re.sub(r"^\s*[*·•]\s+", "• ", line)
        line = re.sub(r"^(\s*\d+[.)])\s+", r"\1 ", line)
        out.append(line.strip() if line.strip() else "")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text, meta


def split_thread(text: str) -> list[str]:
    """Messages in the order they appear: newest first (Outlook puts replies on top)."""
    parts, last = [], 0
    for m in _SPLIT.finditer(text):
        parts.append(text[last:m.start()])
        last = m.start()
    parts.append(text[last:])
    return [p for p in (x.strip() for x in parts) if p]


def _cap(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _paras(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def clean_title(subject: str) -> str:
    """'RE: TrendAI™ Technical Support Case TM-04116060 P2 : [ABC] Upgrade guidance CRM:110304116060'
    -> '[ABC] Upgrade guidance'"""
    t = (subject or "").replace("\xa0", " ")
    t = re.sub(r"^\s*((re|fw|fwd|aw)\s*:\s*)+", "", t, flags=re.I)
    t = re.sub(r"\bCRM\s*:\s*\d+", "", t, flags=re.I)
    m = re.search(r"\bTM-\d+\b(\s*P\d\b)?\s*[:\-–]?\s*", t, re.I)
    if m:  # everything up to the case number is the support system's prefix
        t = t[m.end():] if m.end() < len(t) else t[:m.start()]
    t = re.sub(r"^.*?technical support( case)?\s*[:\-–]?\s*", "", t, flags=re.I) if re.search(r"technical support", t, re.I) else t
    t = re.sub(r"\b(case\s*)?(created|closed|resolved|updated)\s*:?\s*", "", t, flags=re.I)
    return re.sub(r"\s{2,}", " ", t).strip(" -–:")


def client_tag(subject: str) -> str:
    """'[IFL] TMEE upgrade' -> 'IFL'."""
    m = re.search(r"\[([A-Za-z0-9][\w .&-]{0,30})\]", subject or "")
    return m.group(1).strip() if m else ""


_KEY = re.compile(r"\b(must|recommend(ed)?|not supported|only supported|required|do not|cannot|can't|first|before)\b", re.I)


def key_points(body: str, n: int = 5) -> list[str]:
    """Up to n short sentences carrying the main instructions or warnings, strongest first, in mail order."""
    strong = re.compile(r"\b(must|not supported|only supported|cannot|can't|do not|required part)\b", re.I)
    found = []
    for para in _paras(body):
        if para.startswith("•") or re.match(r"^\d+[.)]\s", para) and len(para) < 90:
            continue  # individual steps and section headings stay in the full answer
        for sent in re.split(r"(?<=[.!])\s+", para):
            sent = sent.strip()
            if 40 <= len(sent) <= 240 and _KEY.search(sent) and not re.search(r"https?://", sent) and sent not in found:
                found.append(sent)
    ranked = sorted(found, key=lambda x: (0 if strong.search(x) else 1, found.index(x)))[:n]
    return sorted(ranked, key=found.index)


def summarize(text: str, subject: str = "") -> dict:
    """Headings first (extract). Anything still empty comes from the thread: the answer with the most substance
    becomes the solution, the customer's first real message (or the case topic) becomes the issue."""
    if not subject:
        m = re.search(r"^\s*subject\s*:\s*(.+)$", text or "", re.I | re.M)
        subject = m.group(1).strip() if m else ""
    got = extract(text, subject)
    title = clean_title(subject) or got["title"]
    got["title"] = title[:300]
    got["client_tag"] = client_tag(subject or title)
    msgs, meta = [], {}
    for raw in split_thread(text or ""):
        body, m = _clean_message(raw)
        meta = {**m, **meta}  # newest wins
        if not body or _ACK.search(raw) and len(body) < 1500:
            continue  # Trend's automatic "case created" mail
        if len(body) < 200 and re.search(r"\b(any update|update on (the|this) case|please update|kindly update|following up|"
                                         r"follow up|gentle reminder|reminder|please close|can be closed|noted|ok thanks|attached)\b",
                                         body, re.I) and not re.search(r"\b(resolved|fixed|workaround|cause|caused)\b", body, re.I):
            continue  # "Please update on the case."
        msgs.append(body)
    if meta.get("product"):
        guess = guess_product(meta["product"])
        got["product"] = guess if guess != "other" else ("endpoint-encryption" if "encryption" in meta["product"].lower() else got["product"])
    if got["product"] == "other" and re.search(r"endpoint encryption|tmee|policyserver|bitlocker", text or "", re.I):
        got["product"] = "endpoint-encryption"
    if not msgs:
        return got
    # The answer: the message that reads most like a fix (long, with steps and fix words).
    def score(b):
        return len(b) * (1 + len(_SOLVED.findall(b)) / 10)
    answer = max(msgs, key=score)
    if not got["solution"]:
        body = answer
        paras = _paras(body)
        if paras and _ASK.search(paras[0]) and len(paras) > 1:
            body = "\n\n".join(paras[1:])
        key = key_points(body)
        got["solution"] = _cap(("In short:\n" + "\n".join("• " + k for k in key) + "\n\nFull answer:\n" if len(key) >= 2 else "") + body, 9000)
    if not got["issue"]:
        older = msgs[msgs.index(answer) + 1:]
        if older:
            got["issue"] = _cap("\n\n".join(_paras(older[-1])[:3]), 900)
        else:  # the question was raised on the portal: use the case topic and the reply's opening line
            topic = re.sub(r"^\[[^\]]+\]\s*", "", title)
            parts = [topic + (f" ({meta['product']})" if meta.get("product") else "")]
            first = _paras(answer)[0] if _paras(answer) else ""
            if _ASK.search(first):
                parts.append(first)
            got["issue"] = "\n\n".join(p for p in parts if p)
    if not got["root_cause"]:
        cause = next((p for b in msgs for p in _paras(b) if _CAUSE.search(p)), "")
        got["root_cause"] = _cap(cause, 600)
    return got
