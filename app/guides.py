"""Step-by-step troubleshooting guides: Markdown files in troubleshooting/guides/<product>/<slug>.md.

Each file starts with a small header between '---' lines (title, category, os, summary, scripts).
The renderer below handles the Markdown the guides use (headings, lists, code blocks, tables, notes,
bold, inline code and links) and escapes everything else, so a guide can never inject HTML.
"""
import html
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "troubleshooting" / "guides"
CATEGORY_ORDER = ["Uninstall", "Logs & diagnostics", "Connectivity", "Install & upgrade", "Performance", "Other"]


def _meta(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[m.end():]


def _inline(s: str) -> str:
    out, last = [], 0
    for m in re.finditer(r"`([^`]+)`", s):  # code spans are escaped and left alone
        out.append(_fmt(s[last:m.start()]))
        out.append(f"<code>{html.escape(m.group(1))}</code>")
        last = m.end()
    out.append(_fmt(s[last:]))
    return "".join(out)


def _fmt(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>', s)
    return s


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render(md: str) -> tuple[str, list[tuple[str, str]]]:
    """Markdown -> (html, [(anchor, heading)] for the 'On this page' list)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, toc, i = [], [], 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            code = "\n".join(lines[i + 1:j])
            out.append(f'<div class="codeblock"><button type="button" class="ghost copy" data-copy>Copy</button>'
                       f'<pre><code>{html.escape(code)}</code></pre></div>')
            i = j + 1
            continue
        m = re.match(r"^(#{2,3})\s+(.*)$", line)
        if m:
            level, text = len(m.group(1)), m.group(2).strip()
            anchor = slug(text)
            if level == 2:
                toc.append((anchor, text))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue
        if line.startswith(">"):
            block = []
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i][1:].strip())
                i += 1
            text = " ".join(block)
            kind = "warn" if re.match(r"\*\*(warning|caution)", text, re.I) else "note"
            out.append(f'<div class="callout {kind}">{_inline(text)}</div>')
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            if rows:
                head = "".join(f"<th>{_inline(c)}</th>" for c in rows[0])
                body = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
                out.append(f'<table class="gtable"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
            continue
        m = re.match(r"^(\s*)(\d+\.|[-*])\s+(.*)$", line)
        if m:
            ordered = m.group(2)[0].isdigit()
            tag = "ol" if ordered else "ul"
            items, sub = [], None
            while i < len(lines):
                m2 = re.match(r"^(\s*)(\d+\.|[-*])\s+(.*)$", lines[i])
                if not m2:
                    if lines[i].strip() == "" and i + 1 < len(lines) and re.match(r"^\s*(\d+\.|[-*])\s", lines[i + 1]):
                        i += 1
                        continue
                    break
                if len(m2.group(1)) >= 2 and items:  # nested item
                    items[-1][1].append(m2.group(3))
                else:
                    items.append([m2.group(3), []])
                i += 1
            html_items = []
            for text, subs in items:
                inner = _inline(text)
                if subs:
                    inner += "<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in subs) + "</ul>"
                html_items.append(f"<li>{inner}</li>")
            out.append(f"<{tag}>{''.join(html_items)}</{tag}>")
            continue
        if line.strip():
            para = [line.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|```|>|\||\s*(\d+\.|[-*])\s)", lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{_inline(' '.join(para))}</p>")
            continue
        i += 1
    return "\n".join(out), toc


def _load(path: Path) -> dict:
    meta, body = _meta(path.read_text(encoding="utf-8"))
    return {"product": path.parent.name, "slug": path.stem, "title": meta.get("title", path.stem.replace("-", " ")),
            "category": meta.get("category", "Other"), "os": meta.get("os", ""), "summary": meta.get("summary", ""),
            "scripts": [s.strip() for s in meta.get("scripts", "").split(",") if s.strip()],
            "tools": [s.strip() for s in meta.get("tools", "").split(",") if s.strip()], "body": body}


def all_guides() -> list[dict]:
    out = [_load(p) for p in sorted(ROOT.glob("*/*.md"))]
    rank = {c: n for n, c in enumerate(CATEGORY_ORDER)}
    return sorted(out, key=lambda g: (rank.get(g["category"], 99), g["product"], g["title"]))


def get(product: str, name: str) -> dict | None:
    if not re.fullmatch(r"[\w-]+", product or "") or not re.fullmatch(r"[\w-]+", name or ""):
        return None
    p = ROOT / product / f"{name}.md"
    return _load(p) if p.is_file() else None


def search(guides: list[dict], q: str) -> list[dict]:
    words = [w for w in re.findall(r"\w+", (q or "").lower()) if len(w) > 1]
    if not words:
        return guides
    return [g for g in guides if all(w in (g["title"] + " " + g["summary"] + " " + g["body"]).lower() for w in words)]
