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
        "deep-security": len(re.findall(r"deep security|workload|\bdsa\b|\bdsm\b|ds_agent|\bds agent|c1ws|cloud one|swp", t)),
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
    r"^\s*<?(https?://|mailto:)\S+>?\s*$|^\s*\[cid:|^\s*<image|replyfwdmessage|^\s*classification:", re.I)
_GREETING = re.compile(r"^\s*(hi|hello|dear|good (morning|afternoon|evening))\b[^.\n]{0,60}[,!]?\s*$", re.I)
_PLEASANTRY = re.compile(r"^\s*(thank you for (your patience|contacting|reaching|the update|sharing)|thanks for|"
                         r"apologies for the delay|sorry for the delay|hope you are|i hope this|a pleasant day|"
                         r"have a (good|great|nice)|this is .{2,40} from .{0,40}(support|team).*(handle|assist|own)|"
                         r"classification:)", re.I)
# A sender's title line ("Sr. Technical Support Engineer, AMEA") starts their signature block.
_TITLE_LINE = re.compile(r"^\s*[\w .,&|/-]{0,40}\b(engineer|specialist|consultant|analyst|manager|architect|"
                         r"lead|director|executive)\b[\w .,&|/()-]{0,40}$", re.I)
_SIGN_OFF = re.compile(r"^\s*(thanks|thank you|regards|best regards|warm regards|kind regards|thanks (and|&) regards|"
                       r"thank you and stay safe|cheers|sincerely)\b[^\n]{0,30}$", re.I)
_ACK = re.compile(r"system generated email|we are reviewing your submission|thank you for contacting|"
                  r"has been (created|logged|registered)|additional resources", re.I)
_SOLVED = re.compile(r"\b(resolv|fix|solution|workaround|work-around|recommend|upgrade|install|apply|"
                     r"(please|kindly) (update|upgrade|install|apply|uninstall|restart|reboot|enable|disable|add|exclude|run)|"
                     r"after (applying|upgrading|updating|installing|restarting|rebooting)|"
                     r"(issue|problem) (is|was|has been) (now )?(fixed|resolved|gone|solved)|hotfix|patch|steps?)\b", re.I)
_CAUSE = re.compile(r"\b(root cause|caused by|cause (is|was|seems)|(was|is|be) due to|because of the|"
                    r"(is|was|are|were) not (starting|working|running|connecting) because)\b", re.I)
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
        if _PLEASANTRY.match(line.strip()):  # drop the polite sentences, keep the rest of the line
            line = " ".join(x for x in re.split(r"(?<=[.!])\s+", line.strip()) if not _PLEASANTRY.match(x))
            if not line.strip():
                continue
        if _SIGN_OFF.match(line) and started:
            break  # name, title, address and social links follow
        if started and _TITLE_LINE.match(line) and out and 0 < len(out[-1].split()) <= 4 and not out[-1].endswith((".", ":")):
            out.pop()  # the name line above the title
            break
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
    """'RE: TrendAI™ Technical Support Case TM-04000002 P2 : [ABC] Upgrade guidance CRM:110300000002'
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


_CHASER = re.compile(r"\b(any update|update on (the|this) case|please update|kindly update|following up|follow up on this|"
                     r"follow-up|gentle reminder|reminder|please close|close this case|can be closed|noted|ok thanks|"
                     r"take the downtime|confirm the downtime|let (me|us) know once|check and follow up)\b", re.I)
_OUTCOME = re.compile(r"\b(resolved|is fixed|fixed now|now working|working (fine|properly|now)|properly working|"
                      r"working as expected|issue is (gone|solved)|has been (resolved|fixed)|successfully|reinstalled|"
                      r"is now (running|online|connected)|started (successfully|now))\b", re.I)


def _who(raw: str, top_sender: str = "") -> tuple[str, str]:
    """('trend' | 'team' | 'client', display name) for one message, from its From: line."""
    from .config import settings
    m = re.search(r"^\s*from:\s*(.+)$", raw, re.I | re.M)
    line = m.group(1) if m else top_sender
    name = re.sub(r"\s*[<;].*$", "", line).strip(" '\"") if line else ""
    low = (line or "").lower()
    if "trendmicro" in low or "trend micro" in low:
        return "trend", name
    if not line:
        return "unknown", ""
    if any(d in low for d in settings.team_domains) or not m:  # the newest mail is from the mailbox owner (us)
        return "team", name
    return "client", name


def _date(raw: str) -> str:
    m = re.search(r"^\s*(sent|received|date)\s*:\s*(?:\w+,?\s+)?(\w+ \d{1,2},? \d{4}|\d{1,2} \w+ \d{4}|\w{3} \d{1,2} \d{4})",
                  raw, re.I | re.M)
    return m.group(2) if m else ""


def summarize(text: str, subject: str = "", sender: str = "") -> dict:
    """Whole mail chain -> case entry. Headings ("Issue:", "Resolution:") win when present. Otherwise the solution
    comes from Trend support's replies and our own team's replies (never the client's): our team's closing
    message gives the outcome, Trend's most detailed reply gives the steps."""
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
        who, name = _who(raw, sender)
        chaser = len(body) < 400 and _CHASER.search(body) and not _OUTCOME.search(body) and not _CAUSE.search(body)
        msgs.append({"who": who, "name": name, "date": _date(raw), "body": body, "chaser": bool(chaser)})
    by_title = guess_product(title)
    if by_title != "other":
        got["product"] = by_title
    elif meta.get("product"):
        guess = guess_product(meta["product"])
        got["product"] = guess if guess != "other" else ("endpoint-encryption" if "encryption" in meta["product"].lower() else got["product"])
    if got["product"] == "other" and re.search(r"endpoint encryption|tmee|policyserver|bitlocker", text or "", re.I):
        got["product"] = "endpoint-encryption"
    real = [m for m in msgs if not m["chaser"]]
    if not real:
        return got

    def score(m):
        return len(m["body"]) * (1 + len(_SOLVED.findall(m["body"])) / 10) * (1.5 if re.search(r"^\s*\d+[.)]\s", m["body"], re.M) else 1)
    trend = [m for m in real if m["who"] in ("trend", "unknown")]
    best = max(trend, key=score) if trend else None
    outcome = next((m for m in real if m["who"] == "team" and _OUTCOME.search(m["body"])), None)
    if not got["solution"]:
        parts = []
        if outcome:
            who = " · ".join(x for x in (outcome["name"], outcome["date"]) if x)
            parts.append(f"Outcome{' (' + who + ')' if who else ''}:\n{_cap(outcome['body'], 700)}")
        if best:
            body = best["body"]
            paras = _paras(body)
            if paras and _ASK.search(paras[0]) and len(paras) > 1:
                body = "\n\n".join(paras[1:])
            key = key_points(body)
            if len(key) >= 2 and len(body) > 1500:
                parts.insert(0, "In short:\n" + "\n".join("• " + k for k in key))
            label = "Steps from Trend support" if re.search(r"^\s*\d+[.)]\s", body, re.M) else "Trend support's answer"
            who = " · ".join(x for x in (best["name"], best["date"]) if x)
            parts.append(f"{label}{' (' + who + ')' if who else ''}:\n{body}")
            others = [m for m in trend if m is not best]
            if others:
                parts.append("Also suggested by Trend:\n" + "\n".join(
                    "• " + _cap(" ".join(_paras(m["body"])[:2]), 260) for m in reversed(others)))
        elif not outcome:  # no Trend reply and no outcome: our team's most detailed reply
            team = [m for m in real if m["who"] == "team"]
            if team:
                parts.append(max(team, key=score)["body"])
        got["solution"] = _cap("\n\n".join(parts), 9000)
    if not got["issue"]:
        asked = [m for m in real if m["who"] in ("team", "client") and m is not outcome]
        first_q = asked[-1] if asked else None
        topic = re.sub(r"^\[[^\]]+\]\s*", "", title)
        if first_q and (not best or real.index(first_q) > real.index(best)) and len(first_q["body"]) > 60:
            got["issue"] = topic + "\n\n" + _cap("\n\n".join(_paras(first_q["body"])[:3]), 900)
        else:  # the problem was described on Trend's portal: use the case topic (+ the reply's opening line)
            parts = [topic + (f" ({meta['product']})" if meta.get("product") else "")]
            first = _paras(best["body"])[0] if best and _paras(best["body"]) else ""
            if _ASK.search(first):
                parts.append(first)
            got["issue"] = "\n\n".join(p for p in parts if p)
    if not got["root_cause"]:
        cause = next((p for m in real for p in _paras(m["body"]) if _CAUSE.search(p)), "")
        got["root_cause"] = _cap(cause, 600)
    return got
