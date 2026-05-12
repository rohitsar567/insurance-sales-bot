"""Programmatically set HF Space secrets via the official HF API.

No browser needed. Reads keys from local .env and pushes them as Space
secrets so the deployed app can authenticate to Sarvam / Voyage / Groq / OpenRouter.

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
SECRETS_TO_SET = ["SARVAM_API_KEY", "VOYAGE_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"]


def main():
    hf_token = os.environ.get("HF_TOKEN")
    if not hf_token:
        print("ERROR: HF_TOKEN missing in .env")
        return 1

    api = HfApi(token=hf_token)
    print(f"Setting {len(SECRETS_TO_SET)} secrets on {REPO_ID}...")

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
                description=f"Set programmatically on {key}",
            )
            print(f"  - {key}: SET (length={len(value)})")
        except Exception as e:
            print(f"  - {key}: FAIL {type(e).__name__}: {e}")

    print()
    print("Done. The Space will rebuild automatically.")
    print(f"Watch progress: https://huggingface.co/spaces/{REPO_ID}?logs=build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
