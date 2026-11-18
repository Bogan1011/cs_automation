"""Latest Trend Business Success articles for Vision One, picked up automatically.

Reads the public product page(s) in TREND_ARTICLE_URLS, finds links to /solution/KA-xxxxxxx articles,
and for each new one reads the article page once to keep a short summary and the solution part.
Only public pages are read. Nothing is sent to Trend except plain page requests.
"""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
from sqlalchemy import select

from . import kb
from .config import settings
from .db import SessionLocal, TrendArticle, utcnow

log = logging.getLogger(__name__)
KA_RE = re.compile(r"/solution/(KA-\d{5,8})", re.I)
UA = "Mozilla/5.0 (compatible; internal-support-console/1.0)"
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "ul", "ol",
          "table", "pre", "td", "th", "dt", "dd"}
_SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg", "form", "button"}


class _Page(HTMLParser):
    """Collects article links and the visible text of a page, one block per line."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.lines: list[str] = []
        self.title = ""
        self._buf: list[str] = []
        self._skip = 0
        self._href = None
        self._atext: list[str] = []
        self._in_title = False
        self._h1: list[str] | None = None
        self.h1 = ""

    def _flush(self):
        t = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if t:
            self.lines.append(t)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        if tag in _BLOCK:
            self._flush()
        if tag == "a":
            self._href, self._atext = dict(attrs).get("href") or "", []
        if tag == "title":
            self._in_title = True
        if tag == "h1" and not self.h1:
            self._h1 = []

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self._flush()
        if tag == "a" and self._href is not None:
            self.links.append((self._href, re.sub(r"\s+", " ", "".join(self._atext)).strip()))
            self._href = None
        if tag == "title":
            self._in_title = False
        if tag == "h1" and self._h1 is not None:
            self.h1 = re.sub(r"\s+", " ", "".join(self._h1)).strip()
            self._h1 = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._href is not None:
            self._atext.append(data)
        if self._h1 is not None:
            self._h1.append(data)
        if not self._skip:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def parse(html: str) -> _Page:
    p = _Page()
    p.feed(html or "")
    p.close()
    return p


def find_articles(html: str, base: str) -> dict[str, dict]:
    """KA number -> {title, url} for every article link on a listing page."""
    found: dict[str, dict] = {}
    for href, text in parse(html).links:
        m = KA_RE.search(href or "")
        if not m:
            continue
        ka = m.group(1).upper()
        cur = found.get(ka)
        if not cur or len(text) > len(cur["title"]):
            found[ka] = {"title": text, "url": urljoin(base, href)}
    return found


def kind_of(title: str) -> str:
    t = (title or "").upper()
    for key, label in (("SECURITY ALERT", "Security alert"), ("SECURITY BULLETIN", "Security alert"),
                       ("ADVISORY", "Advisory"), ("NOTICE", "Notice"), ("KNOWN ISSUE", "Known issue"),
                       ("END OF LIFE", "Notice"), ("EOL", "Notice")):
        if key in t:
            return label
    return "Article"


_SOL = re.compile(r"^(solution|resolution|procedure|workaround|fix|recommended action|recommendation|"
                  r"what should i do|action required|steps)\b[\s:]*", re.I)
_SUM = re.compile(r"^(summary|description|details|problem|issue|symptoms?|overview|background|impact)\b[\s:]*", re.I)
_END = re.compile(r"^(related|attachments?|was this (article|information) helpful|rate this|feedback|"
                  r"premium|support center|need help|contact us|share)\b", re.I)
_DATE = re.compile(r"\b((?:updated|published|last updated|modified)\s*:?\s*)?"
                   r"(\d{1,2}[ -](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[ -]\d{4}|"
                   r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]* \d{1,2},? \d{4}|"
                   r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", re.I)


# Page furniture on Business Success articles: labels, metadata and status lines that aren't content.
_META = re.compile(r"^(id|category|keywords?|product\s*/\s*version( includes)?|last updated|updated|published|"
                   r"under investigation|solution id|article id|rating|views?|ka-\d+)\b\s*:?", re.I)


def is_junk(text: str) -> bool:
    """True for a stored summary/solution that is only page metadata (from the first parser)."""
    t = (text or "").strip()
    return not t or bool(re.match(r"^(id:\s*ka-|under investigation|keywords:)", t, re.I)) or len(re.sub(r"(?im)^(id|category):.*$", "", t).strip()) < 15


def _section(lines: list[str], head: re.Pattern, limit: int) -> str:
    out, on = [], False
    for line in lines:
        if head.match(line) and len(line) < 60:
            on = True
            rest = head.sub("", line).strip()
            if rest:
                out.append(rest)
            continue
        if on and ((_SOL.match(line) or _SUM.match(line)) and len(line) < 40 or _END.match(line)):
            break
        if on:
            out.append(line)
        if sum(len(x) for x in out) > limit:
            break
    text = "\n".join(out).strip()
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def read_article(html: str) -> dict:
    """Title, summary, solution and date from one article page (best effort)."""
    p = parse(html)
    lines = [l for l in p.lines if len(l) > 1]
    title = p.h1 or re.sub(r"\s*[|\-–]\s*Trend.*$", "", p.title).strip()
    try:
        start = lines.index(title) + 1 if title in lines else 0
    except ValueError:
        start = 0
    body = [l for l in lines[start:] if not _META.match(l) and l != title and not l.startswith(title)]
    summary = _section(body, _SUM, 600)
    if not summary:
        summary = " ".join(l for l in body if len(l) > 60)[:600]
    solution = _section(body, _SOL, 1500)
    date = ""
    for line in lines[:80]:
        m = _DATE.search(line)
        if m:
            date = m.group(2)
            break
    return {"title": title, "summary": summary, "solution": solution, "published": date}


def sync(transport: httpx.BaseTransport | None = None, max_new: int = 20) -> int:
    """Look for new articles. Returns how many were added."""
    added = 0
    with httpx.Client(transport=transport, timeout=25, follow_redirects=True, headers={"User-Agent": UA}) as http, \
            SessionLocal() as s:
        for src in settings.article_sources:
            try:
                r = http.get(src)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("article list %s failed: %s", src, exc)
                continue
            for ka, a in find_articles(r.text, str(r.url)).items():
                row = s.get(TrendArticle, ka)
                if row is None:
                    row = TrendArticle(ka=ka, title=a["title"], url=a["url"], kind=kind_of(a["title"]),
                                       product=_product(a["title"]))
                    s.add(row)
                    added += 1
                elif a["title"] and not row.title:
                    row.title = a["title"]
        s.commit()
        for row in s.scalars(select(TrendArticle).where(TrendArticle.fetched_at.is_not(None))):
            if is_junk(row.summary) and is_junk(row.solution):  # read again with the better parser
                row.fetched_at = None
        s.commit()
        todo = s.scalars(select(TrendArticle).where(TrendArticle.fetched_at.is_(None))
                         .order_by(TrendArticle.ka.desc()).limit(max_new)).all()
        for row in todo:
            try:
                r = http.get(row.url)
                r.raise_for_status()
                d = read_article(r.text)
            except httpx.HTTPError as exc:
                log.warning("article %s failed: %s", row.ka, exc)
                continue
            row.title = row.title or d["title"]
            row.summary, row.solution, row.published = d["summary"], d["solution"], d["published"]
            row.kind = kind_of(row.title)
            row.product = _product(row.title + " " + d["summary"])
            row.fetched_at = utcnow()
            s.commit()
    return added


def _product(text: str) -> str:
    p = kb.guess_product(text)
    return "xdr" if p == "other" else p


def run_safely():
    try:
        n = sync()
        if n:
            log.info("%d new Trend articles", n)
    except Exception:  # never let the scheduler die
        log.exception("article sync failed")
