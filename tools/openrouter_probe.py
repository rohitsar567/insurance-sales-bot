"""Probe OpenRouter to see what credits remain / if 402 is still happening."""
import asyncio, httpx, time
from rag.extract import build_extract_prompt, schema_excerpt, read_full_text, EXTRACT_SYSTEM
from pathlib import Path

async def test():
    p = Path('rag/corpus/aditya-birla/activ-secure-cancer-secure__brochure.pdf')
    text = read_full_text(p)
    prompt = build_extract_prompt(text, schema_excerpt(), 'test')
    k = None
    with open('.env') as f:
        for line in f:
            if line.startswith('OPENROUTER_API_KEY='):
                k = line.split('=',1)[1].strip(); break

    # Try a couple of free / cheap models
    for model in ['openai/gpt-oss-120b:free', 'qwen/qwen3-next-80b-a3b-instruct:free', 'z-ai/glm-4.5-air:free', 'nousresearch/hermes-3-llama-3.1-405b:free', 'openai/gpt-oss-20b:free']:
        print(f'\n=== {model} ===')
        body = {
            'model': model,
            'messages': [
                {'role':'system','content':EXTRACT_SYSTEM},
                {'role':'user','content':prompt},
            ],
            'temperature': 0.0,
            'max_tokens': 2048,
        }
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post('https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': f'Bearer {k}', 'Content-Type': 'application/json',
                             'HTTP-Referer': 'https://github.com/rohitsar567/insurance-sales-bot',
                             'X-Title': 'Insurance'},
                    json=body)
                el = time.time()-t0
                print(f'status={r.status_code} elapsed={el:.1f}s')
                if r.status_code == 200:
                    d = r.json()
                    content = d['choices'][0]['message']['content']
                    print(f'finish: {d["choices"][0].get("finish_reason")}, content len: {len(content)}')
                    print('first 300:', content[:300])
                else:
                    print('body:', r.text[:500])
        except Exception as e:
            print(f'EXCEPTION after {time.time()-t0:.1f}s: {type(e).__name__}: {str(e)[:200]}')

asyncio.run(test())
