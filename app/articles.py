"""Latest Trend Business Success articles for Vision One, picked up automatically.

Reads the public product page(s) in TREND_ARTICLE_URLS, finds links to /solution/KA-xxxxxxx articles,
and for each new one reads the article page once to keep a short summary and the solution part.
Only public pages are read. Nothing is sent to Trend except plain page requests.
"""
import logging
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

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


_KA_ANY = re.compile(r"KA-\d{6,8}")
_JSON_STR = r'"((?:[^"\\]|\\.){0,600})"'
_TITLE_KEY = re.compile(r'"([\w.@-]*(?:title|subject|name)[\w.@-]*)"\s*:\s*' + _JSON_STR, re.I)
_DESC_KEY = re.compile(r'"([\w.@-]*(?:description|summary|abstract|snippet|content)[\w.@-]*)"\s*:\s*' + _JSON_STR, re.I)
_VIEW_KEY = re.compile(r'"([\w.@-]*(?:view|rating|helpful|popular|hits)[\w.@-]*)"\s*:\s*"?(\d{1,9})', re.I)
_DATE_KEY = re.compile(r'"([\w.@-]*(?:modified|published|created|updated|date)[\w.@-]*)"\s*:\s*"(\d{4}-\d{2}-\d{2}[^"]{0,20})"', re.I)


def _unjson(v: str) -> str:
    try:
        import json
        v = json.loads('"' + v + '"')
    except ValueError:
        pass
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_unescape(v))).strip()


def _encoded(v: str) -> bool:
    return bool(re.search(r"%[0-9A-F]{2}|\w\+\w+\+\w", v))


def _plain(v: str) -> str:
    """'How+to+fix+%22Auto-reconnect%22' -> 'How to fix "Auto-reconnect"'."""
    if _encoded(v):
        from urllib.parse import unquote_plus
        v = unquote_plus(v)
    return re.sub(r"\s+", " ", v.replace("\xa0", " ")).strip()


def html_unescape(v: str) -> str:
    import html as _h
    return _h.unescape(v)


def _enclosing_object(text: str, p: int, reach: int = 4000) -> tuple[int, int] | None:
    """Start/end of the JSON object {...} that contains position p, if any."""
    depth, i = 0, p
    while i > max(0, p - reach):
        i -= 1
        c = text[i]
        if c == "}":
            depth += 1
        elif c == "{":
            if depth == 0:
                break
            depth -= 1
    else:
        return None
    if text[i] != "{":
        return None
    depth, j = 0, i
    while j < min(len(text), i + reach * 2):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i, j + 1
        j += 1
    return None


def _segments(raw: str) -> list[tuple[str, str, int, bool]]:
    """Each article's chunk of embedded data: the JSON object around a KA number, or the markup between
    it and its neighbours."""
    text = html_unescape(raw) if "&quot;" in raw else raw
    pos = [m.start() for m in _KA_ANY.finditer(text)]
    out = []
    for i, p in enumerate(pos):
        obj = _enclosing_object(text, p)
        if obj and obj[1] - obj[0] < 8000 and len(_KA_ANY.findall(text[obj[0]:obj[1]])) <= 3:
            lo, hi = obj
            out.append((text[p:p + 12], text[lo:hi], p - lo, True))
            continue
        else:
            lo = (pos[i - 1] + p) // 2 if i else max(0, p - 900)
            hi = pos[i + 1] if i + 1 < len(pos) else min(len(text), p + 900)
            lo, hi = max(lo, p - 900), min(hi, p + 900)
        seg = text[lo:hi]
        cut = seg.find("<script", p - lo)  # markup mode: never read into a script block
        out.append((text[p:p + 12], seg[:cut] if cut > 0 else seg, p - lo, False))
    return out


def find_embedded(raw: str, base: str) -> dict[str, dict]:
    """Articles that are in the page as data (JSON or hidden markup) rather than as links.
    Trend's product pages ship the whole list in the page and split it into pages in the browser."""
    found: dict[str, dict] = {}
    for ka_raw, seg, at, is_obj in _segments(raw):
        ka = _KA_ANY.match(ka_raw).group(0).upper()
        cur = found.setdefault(ka, {"title": "", "url": urljoin(base, f"/solution/{ka}"), "summary": "", "views": None,
                                    "date": ""})
        if not is_obj:
            seg = re.sub(r"<script.*?(</script>|$)", " ", seg, flags=re.S)
        titles = [(abs(m.start() - at), _unjson(m.group(2))) for m in _TITLE_KEY.finditer(seg)] if is_obj else []
        titles = [(d + (500 if _encoded(t) else 0), _plain(t)) for d, t in titles]  # prefer a readable title
        titles = [t for t in titles if 8 <= len(t[1]) <= 300 and not _KA_ANY.fullmatch(t[1]) and not t[1].startswith("http")]
        if titles and not cur["title"]:
            cur["title"] = min(titles)[1]
        if not cur["title"]:  # markup: the longest readable text right around the number
            bits = [b.strip() for b in re.split(r"<[^>]+>", seg) if b.strip()]
            bits = [html_unescape(b) for b in bits if 15 <= len(b) <= 300 and not re.search(r"[{};=]|function|var ", b)]
            if bits:
                cur["title"] = max(bits, key=len)
        if not is_obj:
            continue
        descs = [_unjson(m.group(2)) for m in _DESC_KEY.finditer(seg)]
        descs = [d for d in descs if len(d) > 30]
        if descs and not cur["summary"]:
            cur["summary"] = max(descs, key=len)[:600]
        counts = [(("view" in m.group(1).lower()), int(m.group(2))) for m in _VIEW_KEY.finditer(seg)]
        if counts:
            pref = [n for is_view, n in counts if is_view] or [n for _, n in counts]
            cur["views"] = max(max(pref), cur["views"] or 0)
        m = _DATE_KEY.search(seg)
        if m and not cur["date"]:
            cur["date"] = m.group(2)[:10]
    return {k: v for k, v in found.items() if v["title"]}


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


SOURCE_LABELS = {"vision-one": "Vision One", "endpoint-workload": "Endpoint & Workload (Cloud One)",
                 "apex-saas": "Apex One SaaS"}
_SOURCE_KEYS = {"trend-vision-one": "vision-one", "cloud-one---endpoint-and-workload-security": "endpoint-workload",
                "apex-one-as-a-service": "apex-saas"}


def source_key(url: str) -> str:
    name = (parse_qs(urlparse(url).query).get("name") or [""])[0]
    return _SOURCE_KEYS.get(name, name[:30] or "other")


def page_links(html: str, base: str, limit: int = 25) -> list[str]:
    """Other pages of the same product listing (?name=...&page=2 and similar), if the page links them."""
    src = urlparse(base)
    name = parse_qs(src.query).get("name")
    out = []
    for href, _ in parse(html).links:
        u = urlparse(urljoin(base, href or ""))
        q = parse_qs(u.query)
        if u.path == src.path and q.get("name") == name and any("page" in k.lower() or k.lower() in ("p", "skip") for k in q):
            full = u.geturl()
            if full not in out and full != base:
                out.append(full)
    return out[:limit]


def sync(transport: httpx.BaseTransport | None = None, max_new: int = 30, progress=None) -> int:
    """Look for new articles on every product page. Returns how many were added."""
    added = 0
    now = utcnow()
    with httpx.Client(transport=transport, timeout=25, follow_redirects=True, headers={"User-Agent": UA}) as http, \
            SessionLocal() as s:
        for src in settings.article_sources:
            key = source_key(src)
            try:
                r = http.get(src)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("article list %s failed: %s", src, exc)
                continue
            first = find_articles(r.text, str(r.url))  # the page's own links = Trend's highlighted advisories
            found = dict(first)
            for ka, a in find_embedded(r.text, str(r.url)).items():  # the full list shipped in the page as data
                found.setdefault(ka, a)
                for k in ("summary", "views", "date"):
                    found[ka].setdefault(k, a[k])
            for extra in page_links(r.text, str(r.url)):
                try:
                    pr = http.get(extra)
                    pr.raise_for_status()
                    for ka, a in find_articles(pr.text, str(pr.url)).items():
                        found.setdefault(ka, a)
                except httpx.HTTPError as exc:
                    log.warning("article page %s failed: %s", extra, exc)
            for row in s.scalars(select(TrendArticle).where(TrendArticle.pinned.is_(True))):
                if key in (row.sources or "").split() and row.ka not in first:
                    row.pinned = False
            for ka, a in found.items():
                row = s.get(TrendArticle, ka)
                if row is None:
                    row = TrendArticle(ka=ka, title=a["title"], url=a["url"], kind=kind_of(a["title"]),
                                       product=_product(a["title"]), sources="")
                    s.add(row)
                    added += 1
                elif a["title"] and (not row.title or _encoded(row.title)):
                    row.title = a["title"]
                row.title = _plain(row.title or "")[:400]
                if key not in (row.sources or "").split():
                    row.sources = ((row.sources or "") + " " + key).strip()
                if ka in first:
                    row.pinned = True
                if a.get("views") is not None:
                    row.views = a["views"]
                if a.get("summary") and not row.summary:
                    row.summary = a["summary"]
                if a.get("date") and not row.published:
                    row.published = a["date"]
                row.last_listed = now
            s.commit()
            if progress:
                progress(f"{key}: {len(found)} articles listed")
        for row in s.scalars(select(TrendArticle).where(TrendArticle.fetched_at.is_not(None))):
            if is_junk(row.summary) and is_junk(row.solution):  # read again with the better parser
                row.fetched_at = None
        s.commit()
        todo = s.scalars(select(TrendArticle).where(TrendArticle.fetched_at.is_(None))
                         .order_by(TrendArticle.ka.desc()).limit(max_new)).all()
        for n, row in enumerate(todo, 1):
            if progress:
                progress(f"reading article {n}/{len(todo)}: {row.ka}")
            try:
                r = http.get(row.url)
                r.raise_for_status()
                d = read_article(r.text)
            except httpx.HTTPError as exc:
                log.warning("article %s failed: %s", row.ka, exc)
                continue
            row.title = row.title or d["title"]
            row.summary = d["summary"] or row.summary
            row.solution, row.published = d["solution"], d["published"] or row.published
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


def check(transport: httpx.BaseTransport | None = None) -> str:
    """What the reader finds on each product page right now (for `python -m app.cli articles-check`)."""
    lines = []
    with httpx.Client(transport=transport, timeout=25, follow_redirects=True, headers={"User-Agent": UA}) as http:
        for src in settings.article_sources:
            try:
                r = http.get(src)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                lines.append(f"{src}\n  FAILED: {exc}")
                continue
            links, data = find_articles(r.text, str(r.url)), find_embedded(r.text, str(r.url))
            raw = len(set(_KA_ANY.findall(r.text)))
            lines.append(f"{source_key(src)}: {len(r.text) // 1024} KB, {raw} KA numbers in the page, "
                         f"{len(links)} as links, {len(data)} read from page data, "
                         f"{sum(1 for a in data.values() if a['views'] is not None)} with a view count")
            for ka, a in list(data.items())[:4]:
                lines.append(f"  {ka}  {a['title'][:90]!r}  views={a['views']} date={a['date']!r}")
    return "\n".join(lines)
