"""Security gates for user-uploaded content.

The /api/upload-policy endpoint accepts arbitrary PDFs from the public web.
That's a real attack surface. Each upload runs through these gates before
we touch it with the embedding model or LLM:

  Gate 1 — FILE MECHANICS
    - magic bytes start with %PDF
    - size <= 25 MB
    - no embedded JavaScript / AcroForm / OpenAction (PDF exploits)
    - no /Launch / /EmbeddedFile actions (file execution / payload smuggling)

  Gate 2 — CONTENT QUALITY
    - at least 1500 chars of extractable text (rejects scanned image-only PDFs
      and intentional empty/garbage uploads)
    - at least 3 pages (an insurance policy is never one page)
    - text contains at least one insurance-domain keyword (cheap filter for
      "this is not a recipe / resume / random PDF")

  Gate 3 — PROMPT INJECTION DEFENSE
    - regex-scan for known injection patterns in extracted text
    - reject if the PDF tries to override the system prompt, expose secrets,
      or impersonate the assistant
    - we DO NOT silently rewrite the content — block + log instead, so the
      user knows their upload was rejected for a reason

  Gate 4 — RATE LIMITING
    - per-session: max 5 uploads / hour
    - cumulative: max 200 chunks across all user uploads (prevents corpus
      flooding by a single session)

Every block is logged to logs/upload_blocks.jsonl with the reason.
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import settings

LOG_DIR = settings.CORPUS_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
UPLOAD_BLOCK_LOG = LOG_DIR / "upload_blocks.jsonl"


@dataclass
class UploadVerdict:
    accepted: bool
    reasons: list[str] = field(default_factory=list)
    extracted_text_chars: int = 0
    page_count: int = 0


# Insurance-domain keywords used to confirm the PDF is plausibly a policy.
# A single hit on any of these is enough — we don't want to over-reject.
INSURANCE_KEYWORDS = (
    "insur", "policy", "premium", "sum insured", "claim", "hospital",
    "coverage", "covered", "exclus", "waiting period", "pre-existing",
    "irdai", "deductible", "cashless", "ayush", "domiciliary", "ncb",
    "no claim bonus", "renewal", "uin", "section", "covered",
    "insurance", "insurer", "insurer's"
)

# Prompt-injection patterns we explicitly reject. Not exhaustive — but every
# one of these is a clear signal of adversarial content, not a real policy.
INJECTION_PATTERNS = [
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions", re.IGNORECASE),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions", re.IGNORECASE),
    re.compile(r"\bforget\s+(?:everything|all|your|the)\s+(?:previous|prior|above|instructions)", re.IGNORECASE),
    re.compile(r"\byou\s+are\s+now\s+a\s+", re.IGNORECASE),
    re.compile(r"\bact\s+as\s+(?:a|an)\s+(?:different|new)\s+", re.IGNORECASE),
    re.compile(r"\bpretend\s+(?:to\s+be|you\s+are)\s+", re.IGNORECASE),
    re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE),
    re.compile(r"<\s*\|?im_start\|?\s*>|<\s*\|?im_end\|?\s*>", re.IGNORECASE),
    re.compile(r"\bjailbreak\b|\bdan\s+mode\b", re.IGNORECASE),
    re.compile(r"reveal\s+(?:your|the)\s+(?:system|hidden|secret|original)\s+(?:prompt|instructions)", re.IGNORECASE),
    re.compile(r"\bapi[_\- ]?key\b.{0,40}(?:reveal|share|tell|print|output)", re.IGNORECASE),
]

# PDF byte patterns that indicate active content / exploits
DANGEROUS_PDF_FEATURES = [
    (rb"/JavaScript", "embedded_javascript"),
    (rb"/JS ", "javascript_action"),
    (rb"/JS\n", "javascript_action_newline"),
    (rb"/Launch", "launch_action"),
    (rb"/EmbeddedFile", "embedded_file"),
    (rb"/OpenAction", "openaction_trigger"),
    (rb"/SubmitForm", "form_submission"),
    (rb"/AA<<", "auto_actions"),
    (rb"/RichMedia", "rich_media_embed"),
    (rb"/Movie", "movie_embed"),
    (rb"/Sound", "sound_embed"),
    (rb"/GoToR", "external_goto_action"),
]

# Magic-byte signatures for executables hidden inside PDFs.
# A PDF should NEVER contain these in its body.
EXECUTABLE_SIGNATURES = [
    (b"MZ\x90\x00", "windows_pe_executable"),    # Windows PE (.exe, .dll)
    (b"\x7fELF", "linux_elf_executable"),         # Linux ELF
    (b"\xcf\xfa\xed\xfe", "macos_macho"),         # macOS Mach-O
    (b"\xca\xfe\xba\xbe", "java_class_or_macho"), # Java class file or Mach-O fat
    (b"#!/", "shell_script_shebang"),             # Shell script
    (b"<script", "html_script_tag"),              # HTML/JS payload
    (b"<?php", "php_payload"),
]

# Per-IP rate limit (memory; v2 → Redis)
ip_uploads: dict[str, list[float]] = defaultdict(list)
IP_UPLOADS_PER_HOUR = 10


# Per-session rate-limit state (in-memory; resets on restart).
# In v2 this moves to Redis.
class RateLimit:
    def __init__(self):
        self.uploads_by_session: dict[str, list[float]] = defaultdict(list)
        self.chunks_by_session: dict[str, int] = defaultdict(int)

    def check_upload_rate(self, session_id: str) -> Optional[str]:
        now = time.time()
        # Keep last hour only
        self.uploads_by_session[session_id] = [
            t for t in self.uploads_by_session[session_id] if now - t < 3600
        ]
        if len(self.uploads_by_session[session_id]) >= 5:
            return "rate_limit_uploads_per_hour"
        return None

    def record_upload(self, session_id: str, chunks: int):
        self.uploads_by_session[session_id].append(time.time())
        self.chunks_by_session[session_id] += chunks

    def check_chunk_quota(self, session_id: str) -> Optional[str]:
        if self.chunks_by_session.get(session_id, 0) >= 200:
            return "rate_limit_total_chunks"
        return None


rate_limiter = RateLimit()


def gate_pdf_mechanics(content: bytes) -> list[str]:
    """Gate 1 — bytes-level PDF checks. Now ALSO scans for executable
    signatures hidden inside PDF body (Windows PE, Linux ELF, Mach-O,
    shell scripts, HTML/JS payloads, PHP). A real insurance policy PDF
    never contains these byte patterns.
    """
    reasons: list[str] = []
    if not content.startswith(b"%PDF"):
        reasons.append("not_a_pdf_magic_bytes")
        return reasons  # no point checking further
    if len(content) > 25 * 1024 * 1024:
        reasons.append("file_too_large_25mb")
    if len(content) < 5_000:
        reasons.append("file_too_small_5kb")

    # Verify the trailing %%EOF is present (well-formed PDF)
    if b"%%EOF" not in content[-256:]:
        reasons.append("malformed_pdf_missing_eof")

    # Look for dangerous PDF features in the WHOLE file (not just first 2MB)
    for needle, label in DANGEROUS_PDF_FEATURES:
        if needle in content:
            reasons.append(f"dangerous_pdf_feature: {label}")

    # Scan for embedded executables / payloads
    for sig, label in EXECUTABLE_SIGNATURES:
        # Don't check the first 8 bytes (false positive on PDF magic)
        if sig in content[8:]:
            reasons.append(f"embedded_executable: {label}")

    return reasons


def gate_ip_rate_limit(ip: str) -> list[str]:
    """Gate 5 — per-IP rate limit on top of per-session."""
    if not ip:
        return []
    now = time.time()
    ip_uploads[ip] = [t for t in ip_uploads[ip] if now - t < 3600]
    if len(ip_uploads[ip]) >= IP_UPLOADS_PER_HOUR:
        return ["rate_limit_per_ip_per_hour"]
    return []


def record_ip_upload(ip: str):
    if ip:
        ip_uploads[ip].append(time.time())


def gate_content_quality(text: str, page_count: int) -> list[str]:
    """Gate 2 — extracted-text checks."""
    reasons: list[str] = []
    if len(text.strip()) < 1500:
        reasons.append(f"too_little_text: {len(text)} chars")
    if page_count < 3:
        reasons.append(f"too_few_pages: {page_count}")

    text_l = text.lower()
    if not any(kw in text_l for kw in INSURANCE_KEYWORDS):
        reasons.append("no_insurance_keywords_found")

    return reasons


def gate_prompt_injection(text: str) -> list[str]:
    """Gate 3 — scan for injection patterns."""
    reasons: list[str] = []
    for pat in INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            snippet = text[max(0, m.start() - 30): m.end() + 30].replace("\n", " ")
            reasons.append(f"injection_pattern: {snippet[:100]}")
            break  # one hit is enough
    return reasons


def gate_rate_limit(session_id: str) -> list[str]:
    """Gate 4 — per-session rate limits."""
    reasons: list[str] = []
    if r := rate_limiter.check_upload_rate(session_id):
        reasons.append(r)
    if r := rate_limiter.check_chunk_quota(session_id):
        reasons.append(r)
    return reasons


def check_upload(
    content: bytes,
    extracted_text: str,
    page_count: int,
    session_id: str = "anonymous",
    ip: str = "",
) -> UploadVerdict:
    """Run all 5 gates. Return verdict with reasons (empty if accepted)."""
    reasons: list[str] = []
    reasons.extend(gate_rate_limit(session_id))
    reasons.extend(gate_ip_rate_limit(ip))
    reasons.extend(gate_pdf_mechanics(content))
    reasons.extend(gate_content_quality(extracted_text, page_count))
    reasons.extend(gate_prompt_injection(extracted_text))

    verdict = UploadVerdict(
        accepted=(len(reasons) == 0),
        reasons=reasons,
        extracted_text_chars=len(extracted_text),
        page_count=page_count,
    )

    if not verdict.accepted:
        _log_block(session_id, reasons, len(content), len(extracted_text), page_count)

    return verdict


def _log_block(session_id: str, reasons: list[str], byte_size: int, text_chars: int, pages: int):
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "reasons": reasons,
        "byte_size": byte_size,
        "text_chars": text_chars,
        "pages": pages,
    }
    with open(UPLOAD_BLOCK_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
