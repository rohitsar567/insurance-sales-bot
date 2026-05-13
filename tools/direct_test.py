"""Direct A/B test: Groq vs Sarvam with new compact prompt."""
import asyncio, time, httpx
from rag.extract import build_extract_prompt, schema_excerpt, read_full_text, EXTRACT_SYSTEM, json_from_llm_text
from backend.providers.sarvam_llm import SarvamLLM
from backend.providers.groq_llm import GroqLLM
from backend.providers.base import ChatMessage
from pathlib import Path

async def test_one(llm_cls, name, p):
    text = read_full_text(p)
    prompt = build_extract_prompt(text, schema_excerpt(), 'test')
    msgs = [ChatMessage(role='system',content=EXTRACT_SYSTEM), ChatMessage(role='user',content=prompt)]
    llm = llm_cls()
    t0 = time.time()
    try:
        r = await llm.chat(messages=msgs, temperature=0.0, max_tokens=2048)
        el = time.time()-t0
        print(f'{name} OK in {el:.1f}s, len={len(r.text)}')
        try:
            j = json_from_llm_text(r.text)
            print(f'  json keys: {len(j)}, sample: {list(j.keys())[:5]}')
        except Exception as e:
            print(f'  parse FAIL: {type(e).__name__}: {e}')
            print(f'  first 300 chars: {r.text[:300]}')
            print(f'  last 300 chars: {r.text[-300:]}')
    except Exception as e:
        el = time.time()-t0
        print(f'{name} FAIL in {el:.1f}s: {type(e).__name__}: {str(e)[:200]}')

async def main():
    p = Path('rag/corpus/aditya-birla/activ-secure-cancer-secure__brochure.pdf')
    print('=== GROQ ===')
    await test_one(GroqLLM, 'groq', p)
    print('\n=== SARVAM ===')
    await test_one(SarvamLLM, 'sarvam', p)

asyncio.run(main())
