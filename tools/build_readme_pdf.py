"""Render README.md → a polished, visually-engaging README PDF.

Pure-Python: the `markdown-pdf` package (pymupdf-backed) — no system deps
like cairo/pango, so it runs anywhere without Homebrew/apt.

This builder does NOT alter the README's wording, ordering, details or
context. It only changes presentation: it prepends a cover page, inserts
an auto-generated clickable table of contents, starts every top-level
("## ") section on a fresh page, and applies a refined typographic theme
so the document reads like a professionally typeset report rather than a
raw markdown dump.

Run:
  uv pip install --python ~/.cache/uv-venvs/insurance-sales-bot/bin/python markdown-pdf
  python tools/build_readme_pdf.py

Output: ~/Desktop/Insurance-Sales-Bot-README.pdf (sharable artifact;
the HF Space repo rejects binaries, so it is never committed).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from markdown_pdf import MarkdownPdf, Section

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
OUT_PATH = Path.home() / "Desktop" / "Insurance-Sales-Bot-README.pdf"


def strip_yaml_frontmatter(text: str) -> str:
    """HF Spaces' YAML metadata at the top of README is meaningless in the PDF."""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip()


def strip_anchor_links(text: str) -> str:
    """Convert in-page `[Text](#anchor)` → `Text` so the pymupdf backend does
    not choke resolving local targets. External `[Text](https://…)` links and
    the auto-generated TOC stay fully clickable."""
    return re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)


def extract_title(text: str) -> tuple[str, str]:
    """Pull the first H1 as the cover title and the first non-empty line
    after it as the subtitle, so the cover reflects the README verbatim
    (no invented copy)."""
    title, subtitle = "Insurance Sales Bot", ""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            title = ln[2:].strip()
            for nxt in lines[i + 1:]:
                s = nxt.strip()
                if s and not s.startswith("#"):
                    subtitle = re.sub(r"[*_`]", "", s)
                    break
            break
    return title, subtitle


# Cover page is pure presentation; it restates the README's own H1/subtitle.
COVER_TEMPLATE = """
<div class="cover">
  <div class="cover-kicker">PROJECT DOCUMENTATION</div>
  <h1 class="cover-title">{title}</h1>
  <div class="cover-sub">{subtitle}</div>
  <div class="cover-rule"></div>
  <div class="cover-meta">Author&nbsp;&nbsp;·&nbsp;&nbsp;Rohit Saraf</div>
  <div class="cover-meta">Generated&nbsp;&nbsp;·&nbsp;&nbsp;{date}</div>
  <div class="cover-foot">A faithful PDF rendering of README.md — same content, same order.</div>
</div>
"""

CSS = """
body { font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif;
       font-size: 10.8pt; color: #1f2937; line-height: 1.62; }

/* ---- Cover page ---- */
.cover { text-align: center; padding-top: 150px; page-break-after: always; }
.cover-kicker { font-size: 11pt; letter-spacing: 5px; color: #0f766e; font-weight: 700; }
.cover-title { font-size: 32pt; color: #0f172a; margin: 26px 40px 10px; border: none;
               line-height: 1.18; }
.cover-sub { font-size: 13.5pt; color: #475569; margin: 0 60px; font-style: italic; }
.cover-rule { width: 90px; height: 4px; background: #0f766e; margin: 34px auto; border-radius: 2px; }
.cover-meta { font-size: 11pt; color: #334155; margin: 4px 0; }
.cover-foot { font-size: 9.5pt; color: #94a3b8; margin-top: 150px; }

/* ---- Headings: each top-level section on its own page ---- */
h1 { font-size: 21pt; color: #0f172a; margin: 0.4em 0 0.3em; border-bottom: 2px solid #0f766e;
     padding-bottom: 5px; }
h2 { font-size: 16pt; color: #134e4a; margin: 1.3em 0 0.45em; border-bottom: 1px solid #cbd5e1;
     padding-bottom: 4px; page-break-before: always; }
h3 { font-size: 12.8pt; color: #0f766e; margin: 1.15em 0 0.3em; }
h4 { font-size: 11.2pt; color: #1e3a8a; margin: 0.95em 0 0.3em; }

p  { margin: 0.5em 0; }
a  { color: #0d6efd; text-decoration: none; }
strong { color: #0f172a; }
em { color: #334155; }

code { font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; font-size: 9pt;
       background: #f1f5f9; padding: 1px 4px; border-radius: 3px; color: #b91c1c; }
pre  { font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; font-size: 8.8pt;
       background: #f8fafc; border: 1px solid #e2e8f0; border-left: 3px solid #0f766e;
       padding: 10px 12px; border-radius: 4px; line-height: 1.45; }
pre code { background: transparent; padding: 0; color: #0f172a; }

blockquote { border-left: 3px solid #0f766e; margin: 0.9em 0; padding: 6px 14px;
             color: #475569; background: #f0fdfa; border-radius: 0 4px 4px 0; }

/* The README is pure markdown (no tables by design); style defensively anyway. */
table { border-collapse: collapse; width: 100%; margin: 0.7em 0; font-size: 9.6pt; }
th, td { border: 1px solid #cbd5e1; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background: #f1f5f9; font-weight: 600; color: #0f172a; }

ul, ol { padding-left: 22px; margin: 0.45em 0; }
li { margin: 0.28em 0; }
hr { border: none; border-top: 1px solid #cbd5e1; margin: 1.6em 0; }
"""


def main() -> None:
    raw = README_PATH.read_text(encoding="utf-8")
    body = strip_yaml_frontmatter(raw)
    title, subtitle = extract_title(body)
    cleaned = strip_anchor_links(body)

    cover = COVER_TEMPLATE.format(
        title=title,
        subtitle=subtitle or "Voice-first AI advisor for Indian health insurance",
        date=_dt.date.today().strftime("%d %B %Y"),
    )

    pdf = MarkdownPdf(toc_level=3, optimize=True)
    pdf.meta["title"] = f"{title} — README"
    pdf.meta["author"] = "Rohit Saraf"
    pdf.meta["subject"] = subtitle or "Insurance Sales Bot — documentation"
    # Cover first (no TOC entry), then the README verbatim with a clickable TOC.
    pdf.add_section(Section(cover, toc=False), user_css=CSS)
    pdf.add_section(Section(cleaned, toc=True), user_css=CSS)
    pdf.save(str(OUT_PATH))

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {OUT_PATH}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
