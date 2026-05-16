"""Smoke-test every provider before building anything on top.

Run from project root:
  python -m backend.providers._smoke_test

Each test prints OK/FAIL and the response. Failures here will surface in the
build before they surface in the UI.

Stack A providers (post-2026-05-14, D-019):
  - Sarvam-M LLM — Indic translation (Hindi/Hinglish/vernacular)
  - Sarvam Bulbul TTS — voice synthesis
  - Sarvam Saarika STT — voice recognition
  - Local BGE embeddings (no network)
  - NVIDIA NIM brain — DeepSeek-V4-Pro
"""

from __future__ import annotations

import asyncio
import traceback

from backend.config import settings
from backend.providers.base import ChatMessage
from backend.providers.nvidia_nim_llm import get_brain_llm
from backend.providers.sarvam_llm import SarvamLLM
from backend.providers.sarvam_stt import SarvamSTT
from backend.providers.sarvam_tts import SarvamTTS


async def test_sarvam_llm():
    print("\n--- Sarvam-M LLM (Indic translation only) ---")
    try:
        client = SarvamLLM()
        result = await client.chat(
            messages=[
                ChatMessage(role="system", content="You are a translator. Translate to Hindi."),
                ChatMessage(role="user", content="The sum insured is the maximum amount your policy will pay."),
            ],
            max_tokens=120,
        )
        print(f"OK | model={result.model} | reply: {result.text[:200]}")
        print(f"   tokens prompt={result.prompt_tokens} completion={result.completion_tokens}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_sarvam_tts():
    print("\n--- Sarvam Bulbul TTS ---")
    try:
        client = SarvamTTS()
        audio = await client.synthesize(
            text="Hello, I am your insurance advisor.",
            language_code="en-IN",
        )
        print(f"OK | got {len(audio)} bytes of audio")
        out = settings.CORPUS_DIR.parent / "_smoke_tts.wav"
        out.write_bytes(audio)
        print(f"   saved to {out.relative_to(settings.CORPUS_DIR.parent.parent)}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_nim_brain():
    print("\n--- NIM DeepSeek-V4-Pro (THE brain — Stack A primary) ---")
    try:
        client = get_brain_llm()
        result = await client.chat(
            messages=[
                ChatMessage(role="system", content="You are a precise insurance advisor."),
                ChatMessage(role="user", content="Briefly: what does 'sum insured' mean in health insurance? Under 25 words."),
            ],
            max_tokens=120,
            temperature=0.2,
        )
        print(f"OK | model={result.model} | reply: {result.text[:200]}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_sarvam_stt():
    """STT needs an audio file. We reuse the TTS output if it ran successfully."""
    print("\n--- Sarvam Saarika STT ---")
    try:
        audio_path = settings.CORPUS_DIR.parent / "_smoke_tts.wav"
        if not audio_path.exists():
            print("SKIP | no _smoke_tts.wav (TTS must run first)")
            return False
        audio_bytes = audio_path.read_bytes()
        client = SarvamSTT()
        result = await client.transcribe(
            audio_bytes=audio_bytes,
            audio_format="wav",
            language_code="en-IN",
        )
        print(f"OK | transcript: {result.text!r}")
        print(f"   language={result.language_code} confidence={result.confidence}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def main():
    missing = settings.validate()
    if missing:
        print(f"WARN | missing keys: {missing}")

    results = {}
    results["nim_brain"] = await test_nim_brain()
    results["sarvam_llm"] = await test_sarvam_llm()
    results["sarvam_tts"] = await test_sarvam_tts()
    results["sarvam_stt"] = await test_sarvam_stt()  # depends on TTS output

    print("\n========== SUMMARY ==========")
    for name, ok in results.items():
        print(f"  {name:>20s}: {'OK' if ok else 'FAIL'}")
    print(f"\n{sum(results.values())}/{len(results)} providers healthy.")


if __name__ == "__main__":
    asyncio.run(main())
