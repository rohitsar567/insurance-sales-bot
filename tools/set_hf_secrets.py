"""Programmatically set HF Space secrets via the official HF API.

No browser needed. Reads keys from local .env and pushes them as Space
secrets so the deployed app can authenticate. Post-D-019 (Stack A
consolidation): only Sarvam + Voyage + NVIDIA NIM keys are required.
Legacy GROQ/OPENROUTER/CEREBRAS/DEEPSEEK keys are deleted from the Space
to prevent confusion (they are no longer referenced by the code).

Run:
  python tools/set_hf_secrets.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

REPO_ID = "rohitsar567/InsuranceBot"
# Active secrets read by the running code (post-D-022 + admin panel):
#   - SARVAM / NIM = the live providers
#   - VOYAGE       = legacy (kept for back-compat with old extracted artifacts)
#   - OPENROUTER / GROQ = optional cross-provider fallbacks (re-added 2026-05-14)
#   - ADMIN_*      = control-panel gate (IP allowlist + password)
SECRETS_TO_SET = [
    "SARVAM_API_KEY", "VOYAGE_API_KEY", "NVIDIA_NIM_API_KEY",
    "OPENROUTER_API_KEY", "GROQ_API_KEY",
    "ADMIN_IP_ALLOWLIST", "ADMIN_PASSWORD",
]
# Truly retired providers — delete from Space to prevent confusion.
SECRETS_TO_DELETE = ["CEREBRAS_API_KEY", "DEEPSEEK_API_KEY"]


def main():
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("ERROR: HF_TOKEN missing in .env")
        return 1

    api = HfApi(token=hf_token)
    print(f"Setting {len(SECRETS_TO_SET)} active secrets on {REPO_ID}...")

    for key in SECRETS_TO_SET:
        value = os.environ.get(key)
        if not value:
            print(f"  - {key}: MISSING in local .env — skipping")
            continue
        try:
            api.add_space_secret(
                repo_id=REPO_ID,
                key=key,
                value=value,
                description=f"Set programmatically — {key}",
            )
            print(f"  - {key}: SET (length={len(value)})")
        except Exception as e:
            print(f"  - {key}: FAIL {type(e).__name__}: {e}")

    print()
    print(f"Deleting {len(SECRETS_TO_DELETE)} retired secrets (D-019 consolidation)...")
    for key in SECRETS_TO_DELETE:
        try:
            api.delete_space_secret(repo_id=REPO_ID, key=key)
            print(f"  - {key}: DELETED")
        except Exception as e:
            # Already-deleted secrets return 4xx — that's the desired end state.
            print(f"  - {key}: not deleted ({type(e).__name__}: {str(e)[:80]})")

    print()
    print("Done. The Space will rebuild automatically.")
    print(f"Watch progress: https://huggingface.co/spaces/{REPO_ID}?logs=build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
