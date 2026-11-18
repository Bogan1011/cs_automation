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
