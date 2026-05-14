"""Render README.md → README.pdf.

Pure-Python: `markdown-pdf` package (pymupdf-backed) — no system deps like
cairo/pango. Trade-off vs WeasyPrint: slightly less CSS-perfect output but
renders all the README's tables, code fences, headings, and links cleanly
without needing Homebrew or apt to install glib/pango libraries.

Run:
  uv pip install --python ~/.cache/uv-venvs/insurance-sales-bot/bin/python markdown-pdf
  python tools/build_readme_pdf.py

Output: README.pdf in the repo root, ready to share / download from GitHub.
"""
from __future__ import annotations

import re
from pathlib import Path

from markdown_pdf import MarkdownPdf, Section

ROOT = Path(__file__).resolve().parent.parent
README_PATH = ROOT / "README.md"
# PDF is a sharable artifact, not a repo asset. Drop it on the Desktop so
# the user can email / Slack / AirDrop it without digging into ~/Developer/.
# HF Space also rejects binaries in the Space repo (Xet/LFS required).
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
    """Convert `[Text](#anchor)` → `Text` so pymupdf doesn't blow up looking
    for the link target. External `[Text](https://…)` links stay intact."""
    return re.sub(r"\[([^\]]+)\]\(#[^)]+\)", r"\1", text)


CSS = """
body { font-family: -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 11pt; color: #1f2937; line-height: 1.55; }
h1 { font-size: 22pt; color: #0f172a; margin-top: 0.5em; margin-bottom: 0.3em; border-bottom: 2px solid #0f766e; padding-bottom: 4px; }
h2 { font-size: 16pt; color: #134e4a; margin-top: 1.4em; margin-bottom: 0.4em; border-bottom: 1px solid #94a3b8; padding-bottom: 3px; }
h3 { font-size: 13pt; color: #0f766e; margin-top: 1.2em; margin-bottom: 0.3em; }
h4 { font-size: 11.5pt; color: #1e3a8a; margin-top: 1em; margin-bottom: 0.3em; }
p  { margin: 0.4em 0; }
a  { color: #0d6efd; text-decoration: none; }
code { font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; font-size: 9.5pt; background-color: #f1f5f9; padding: 1px 4px; border-radius: 3px; color: #b91c1c; }
pre { font-family: 'SF Mono', Menlo, Monaco, Consolas, monospace; font-size: 9.5pt; background-color: #f8fafc; border-left: 3px solid #0f766e; padding: 8px 10px; border-radius: 4px; line-height: 1.4; }
pre code { background: transparent; padding: 0; color: #0f172a; }
blockquote { border-left: 3px solid #94a3b8; margin: 0.8em 0; padding: 4px 12px; color: #475569; background: #f8fafc; }
table { border-collapse: collapse; width: 100%; margin: 0.6em 0; font-size: 10pt; }
th, td { border: 1px solid #cbd5e1; padding: 5px 8px; text-align: left; vertical-align: top; }
th { background-color: #f1f5f9; font-weight: 600; color: #0f172a; }
ul, ol { padding-left: 20px; margin: 0.4em 0; }
li { margin: 0.2em 0; }
hr { border: none; border-top: 1px solid #cbd5e1; margin: 1.5em 0; }
strong { color: #0f172a; }
"""


def main() -> None:
    raw = README_PATH.read_text(encoding="utf-8")
    cleaned = strip_anchor_links(strip_yaml_frontmatter(raw))

    pdf = MarkdownPdf(toc_level=0, optimize=True)
    pdf.meta["title"] = "Insurance Sales Portfolio Expert — README"
    pdf.meta["author"] = "Rohit Saraf"
    pdf.meta["subject"] = "Voice-first AI advisor for Indian health insurance — Sarvam AI takehome"
    pdf.add_section(Section(cleaned, toc=False), user_css=CSS)
    pdf.save(str(OUT_PATH))

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"wrote {OUT_PATH.relative_to(ROOT)}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
