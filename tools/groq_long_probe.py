"""Probe Groq with the actual extract prompt."""
import asyncio, time, httpx
from rag.extract import build_extract_prompt, schema_excerpt, read_full_text, EXTRACT_SYSTEM
from pathlib import Path

async def test():
    p = Path('rag/corpus/aditya-birla/activ-secure-cancer-secure__brochure.pdf')
    text = read_full_text(p)
    prompt = build_extract_prompt(text, schema_excerpt(), 'test')
    k = None
    with open('.env') as f:
        for line in f:
            if line.startswith('GROQ_API_KEY='):
                k = line.split('=',1)[1].strip(); break
    body = {
        'model': 'llama-3.3-70b-versatile',
        'messages': [
            {'role':'system','content':EXTRACT_SYSTEM},
            {'role':'user','content':prompt},
        ],
        'temperature': 0.0,
        'max_tokens': 2048,
    }
    print(f'prompt size: {len(prompt)} chars')
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60) as c:
        try:
            r = await c.post('https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {k}', 'Content-Type': 'application/json'},
                json=body)
            print(f'status={r.status_code} elapsed={time.time()-t0:.1f}s')
            print('rate headers:')
            for h in ('x-ratelimit-remaining-tokens', 'x-ratelimit-remaining-requests', 'retry-after'):
                print(f'  {h}: {r.headers.get(h)}')
            if r.status_code == 200:
                d = r.json()
                content = d['choices'][0]['message']['content']
                print(f'finish: {d["choices"][0].get("finish_reason")}, content len: {len(content)}')
                print('first 300:', content[:300])
            else:
                print('body:', r.text[:500])
        except Exception as e:
            print(f'EXCEPTION after {time.time()-t0:.1f}s: {type(e).__name__}: {e}')

asyncio.run(test())
