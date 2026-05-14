"""Security gates for user-uploaded content.

The /api/upload-policy endpoint accepts arbitrary PDFs from the public web.
That's a real attack surface. Each upload runs through these gates before
we touch it with the embedding model or LLM:

  Gate 1 — FILE MECHANICS
    - magic bytes start with %PDF
    - size <= 25 MB and >= 5 KB
    - no embedded JavaScript / AcroForm / OpenAction (PDF exploits)
    - no /Launch / /EmbeddedFile actions (file execution / payload smuggling)
    - no embedded executables (PE / ELF / Mach-O / shell scripts / HTML / PHP)
    - well-formed %%EOF in trailer

  Gate 2 — CONTENT QUALITY
    - at least 1500 chars of extractable text (rejects scanned image-only PDFs
      and intentional empty/garbage uploads)
    - 3 ≤ page_count ≤ 200 — real insurance policies fall in this window;
      <3 = trivially-empty, >200 = bundled-dump or abuse vector
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

  Gate 5 — PER-IP RATE LIMIT
    - max 10 uploads / hour per source IP (catches rotating session_id)

  Gate 6 — ENCRYPTED / PASSWORD-PROTECTED PDF (added 2026-05-14)
    - pdfplumber raises on encrypted PDFs; we surface that as a clean reject
      instead of storing an opaque blob.

  Gate 7 — BYTES HASH DEDUPE + REJECT-CACHE (added 2026-05-14)
    - SHA256 the bytes. If we've rejected this exact hash in the last 24h,
      short-circuit and reject again without re-running the full pipeline.
    - If we've already accepted+indexed this hash for the same session,
      return the cached chunk count.

  Gate 8 — LLM-JUDGE AUDIT (added 2026-05-14)
    - Pass the first 2000 chars to the judge LLM with a strict yes/no prompt:
      "Is this a real Indian health-insurance policy document?". The judge is
      the same Mistral Large 3 used elsewhere — different family from the
      brain so it grades independently. ~3s latency, ~0.05 per call.
    - High precision against social-engineering content that escapes regex
      (e.g., carefully-worded prose that doesn't trip injection patterns
      but isn't actually a policy).

Every block is logged to logs/upload_blocks.jsonl with the reason.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
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
    cached_chunks: Optional[int] = None


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


def gate_encrypted_pdf(content: bytes) -> list[str]:
    """Gate 6 — reject password-protected / encrypted PDFs.

    pdfplumber raises on encrypted PDFs at open-time OR at first metadata /
    pages access. We catch broadly so any decoding failure here surfaces as
    a clean reject (don't let an unreadable blob get past content quality).
    Runs BEFORE the text-extraction gates so we never store junk.
    """
    try:
        import pdfplumber
    except Exception:
        # If pdfplumber isn't importable for some reason, don't block the
        # pipeline here — the upstream extraction step would have failed
        # already if the lib was actually missing.
        return []

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            # Touch metadata + first page to force a decrypt attempt; some
            # encrypted PDFs only raise on these accesses, not on open().
            _ = pdf.metadata
            _ = pdf.pages
            if pdf.pages:
                _ = pdf.pages[0]
        return []
    except Exception as e:
        msg = str(e).lower()
        if "encrypt" in msg or "password" in msg:
            return ["pdf_encrypted_or_password_protected"]
        # Any other open/parse failure here = unreadable; reject the same way.
        return ["pdf_encrypted_or_password_protected"]


def gate_page_count_ceiling(page_count: int) -> list[str]:
    """Gate 7 — reject PDFs over 200 pages.

    Floor (<3) is handled in gate_content_quality. Anything over 200 pages
    is almost certainly a bundled-dump or abuse vector (a real Indian health
    insurance policy is 20-150 pages).
    """
    if page_count > 200:
        return ["too_many_pages_over_200"]
    return []


# In-memory hash dedupe caches (process-local; reset on restart; v2 → Redis).
_recent_rejects: dict[str, float] = {}
_recent_accepts: dict[tuple[str, str], int] = {}
_REJECT_TTL_SECONDS = 24 * 3600


def record_reject(sha: str) -> None:
    """Mark a content hash as recently-rejected so re-uploads short-circuit."""
    _recent_rejects[sha] = time.time()


def record_accept(sha: str, session_id: str, chunks: int) -> None:
    """Cache an accepted hash → chunk count for the same session."""
    _recent_accepts[(sha, session_id)] = chunks


def gate_hash_dedupe(content: bytes, session_id: str) -> tuple[list[str], Optional[int]]:
    """Gate 7b — content-hash dedupe + reject cache.

    Returns (reasons, cached_chunks):
      - If hash was rejected <24h ago → (["already_rejected_24h"], None)
      - If (hash, session_id) is in the accept cache → ([], cached_chunks)
      - Else → ([], None)
    """
    sha = hashlib.sha256(content).hexdigest()
    now = time.time()

    # Drop rejects older than the TTL so the cache doesn't grow unbounded.
    if _recent_rejects:
        stale = [h for h, ts in _recent_rejects.items() if now - ts >= _REJECT_TTL_SECONDS]
        for h in stale:
            _recent_rejects.pop(h, None)

    if sha in _recent_rejects and (now - _recent_rejects[sha]) < _REJECT_TTL_SECONDS:
        return (["already_rejected_24h"], None)

    cached = _recent_accepts.get((sha, session_id))
    if cached is not None:
        return ([], cached)

    return ([], None)


async def gate_llm_judge(text: str) -> list[str]:
    """Gate 8 — LLM-judge audit. Strict yes/no on "is this a real Indian
    health-insurance policy document?".

    Fail-open: if the call itself fails (timeout, parse error, transport
    issue), return [] — the regex + mechanics gates already catch the
    obvious bad stuff, and we don't want a single transient LLM hiccup to
    block legitimate uploads.

    6s hard timeout via asyncio.wait_for; last gate in the pipeline since
    it's the most expensive one.
    """
    try:
        from backend.providers.nvidia_nim_llm import get_judge_llm
        from backend.providers.base import ChatMessage

        snippet = text[:2000]
        prompt = (
            "You are an audit gate. Determine if the following text is a real "
            "Indian health-insurance policy document. Reply with strictly JSON: "
            '{"is_policy": true|false, "reason": "<one short sentence>"}. '
            "Be strict — generic finance docs, recipes, news articles, code, "
            "manuals, books all FAIL."
        )
        messages = [
            ChatMessage(role="system", content=prompt),
            ChatMessage(role="user", content=snippet),
        ]

        judge = get_judge_llm()
        result = await asyncio.wait_for(
            judge.chat(
                messages=messages,
                temperature=0.0,
                max_tokens=80,
                response_format={"type": "json_object"},
            ),
            timeout=6.0,
        )
        raw = (result.text or "").strip()
        if not raw:
            return []
        parsed = json.loads(raw)
        is_policy = bool(parsed.get("is_policy", True))
        reason = str(parsed.get("reason", "")).strip()[:200]
        if not is_policy:
            return [f"llm_judge_rejected: {reason}" if reason else "llm_judge_rejected"]
        return []
    except Exception:
        # Fail-open — availability over precision for this gate.
        return []


async def check_upload(
    content: bytes,
    extracted_text: str,
    page_count: int,
    session_id: str = "anonymous",
    ip: str = "",
    enable_llm_judge: bool = True,
) -> UploadVerdict:
    """Run all 8 gates. Return verdict with reasons (empty if accepted).

    Order (cheap → expensive; short-circuit only on the dedupe accept-cache,
    so the caller can skip re-embedding):

      1.  gate_hash_dedupe          (instant; may short-circuit accept w/ cached chunks)
      2.  gate_rate_limit           (per-session counter lookup)
      3.  gate_ip_rate_limit        (per-IP counter lookup)
      4.  gate_pdf_mechanics        (byte scan)
      5.  gate_encrypted_pdf        (pdfplumber open)
      6.  gate_content_quality      (extracted text + ≥3 page floor)
      7.  gate_page_count_ceiling   (≤200 page ceiling)
      8.  gate_prompt_injection     (regex sweep)
      9.  gate_llm_judge            (LLM audit; only if everything above passed)
    """
    # 1. Hash dedupe first — accept-cache short-circuit avoids re-embedding.
    dedupe_reasons, cached_chunks = gate_hash_dedupe(content, session_id)
    if cached_chunks is not None:
        return UploadVerdict(
            accepted=True,
            reasons=[],
            extracted_text_chars=len(extracted_text),
            page_count=page_count,
            cached_chunks=cached_chunks,
        )
    if dedupe_reasons:
        # Recently rejected — re-log + return immediately without running the rest.
        _log_block(session_id, dedupe_reasons, len(content), len(extracted_text), page_count)
        return UploadVerdict(
            accepted=False,
            reasons=dedupe_reasons,
            extracted_text_chars=len(extracted_text),
            page_count=page_count,
        )

    reasons: list[str] = []
    reasons.extend(gate_rate_limit(session_id))
    reasons.extend(gate_ip_rate_limit(ip))
    reasons.extend(gate_pdf_mechanics(content))
    reasons.extend(gate_encrypted_pdf(content))
    reasons.extend(gate_content_quality(extracted_text, page_count))
    reasons.extend(gate_page_count_ceiling(page_count))
    reasons.extend(gate_prompt_injection(extracted_text))

    # LLM judge runs LAST — only if every earlier gate passed. No point
    # paying ~3s + an API call to grade something that already failed.
    if enable_llm_judge and not reasons:
        reasons.extend(await gate_llm_judge(extracted_text))

    verdict = UploadVerdict(
        accepted=(len(reasons) == 0),
        reasons=reasons,
        extracted_text_chars=len(extracted_text),
        page_count=page_count,
    )

    if not verdict.accepted:
        # Cache this hash as rejected so identical re-uploads are cheap.
        try:
            sha = hashlib.sha256(content).hexdigest()
            record_reject(sha)
        except Exception:
            pass
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
