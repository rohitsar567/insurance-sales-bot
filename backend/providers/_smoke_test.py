"""Smoke-test every provider before building anything on top.

Run from project root:
  python -m backend.providers._smoke_test

Each test prints OK/FAIL and the response. Failures here will surface in the
build before they surface in the UI.
"""

from __future__ import annotations

import asyncio
import traceback

from backend.config import settings
from backend.providers.base import ChatMessage
from backend.providers.sarvam_llm import SarvamLLM
from backend.providers.sarvam_stt import SarvamSTT
from backend.providers.sarvam_tts import SarvamTTS
from backend.providers.voyage_embeddings import VoyageEmbeddings
from backend.providers.groq_llm import GroqLLM
from backend.providers.openrouter_llm import OpenRouterLLM


async def test_sarvam_llm():
    print("\n--- Sarvam-M LLM ---")
    try:
        client = SarvamLLM()
        result = await client.chat(
            messages=[
                ChatMessage(role="system", content="You are a helpful insurance advisor. Keep replies under 20 words."),
                ChatMessage(role="user", content="What does PED stand for in health insurance?"),
            ],
            max_tokens=100,
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
        # Save for manual inspection
        out = settings.CORPUS_DIR.parent / "_smoke_tts.wav"
        out.write_bytes(audio)
        print(f"   saved to {out.relative_to(settings.CORPUS_DIR.parent.parent)}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_voyage():
    print("\n--- Voyage embeddings ---")
    try:
        client = VoyageEmbeddings()
        vectors = await client.embed(["the cataract waiting period is 24 months", "policy covers ayurveda"])
        print(f"OK | got {len(vectors)} vectors, dim={len(vectors[0])}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_groq():
    print("\n--- Groq Llama-3.3-70B (grader + medium fallback) ---")
    try:
        client = GroqLLM()
        result = await client.chat(
            messages=[
                ChatMessage(role="system", content="You are a strict evaluator. Reply YES or NO only."),
                ChatMessage(role="user", content="Is '24 months' semantically equivalent to '2 years'? YES or NO."),
            ],
            max_tokens=10,
            temperature=0.0,
        )
        print(f"OK | model={result.model} | reply: {result.text!r}")
        return True
    except Exception as e:
        print(f"FAIL | {type(e).__name__}: {e}")
        traceback.print_exc()
        return False


async def test_openrouter():
    print("\n--- OpenRouter DeepSeek-V3 (strongest fallback brain) ---")
    try:
        client = OpenRouterLLM()
        result = await client.chat(
            messages=[
                ChatMessage(role="system", content="You are a precise insurance advisor."),
                ChatMessage(role="user", content="Briefly: what does 'sum insured' mean in health insurance? Under 25 words."),
            ],
            max_tokens=100,
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
    results["sarvam_llm"] = await test_sarvam_llm()
    results["voyage"] = await test_voyage()
    results["groq"] = await test_groq()
    results["openrouter"] = await test_openrouter()
    results["sarvam_tts"] = await test_sarvam_tts()
    results["sarvam_stt"] = await test_sarvam_stt()  # depends on TTS output

    print("\n========== SUMMARY ==========")
    for name, ok in results.items():
        print(f"  {name:>20s}: {'OK' if ok else 'FAIL'}")
    print(f"\n{sum(results.values())}/{len(results)} providers healthy.")


if __name__ == "__main__":
    asyncio.run(main())
