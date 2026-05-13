"""Probe Sarvam-M with current extract prompt to see if 400 still happens.
Direct httpx so we can see the error body."""
import asyncio, httpx
from rag.extract import build_extract_prompt, schema_excerpt, read_full_text, EXTRACT_SYSTEM
from pathlib import Path

async def test():
    p = Path('rag/corpus/aditya-birla/activ-health-individual__wordings.pdf')
    text = read_full_text(p)
    prompt = build_extract_prompt(text, schema_excerpt(), 'test')
    k = None
    with open('.env') as f:
        for line in f:
            if line.startswith('SARVAM_API_KEY='):
                k = line.split('=',1)[1].strip()
                break
    body = {
        'model': 'sarvam-m',
        'messages': [
            {'role':'system','content':EXTRACT_SYSTEM},
            {'role':'user','content':prompt},
        ],
        'temperature': 0.0,
        'max_tokens': 2048,
    }
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post('https://api.sarvam.ai/v1/chat/completions',
            headers={'api-subscription-key': k, 'Authorization': f'Bearer {k}', 'Content-Type': 'application/json'},
            json=body)
        print('status:', r.status_code)
        d = r.json()
        content = d['choices'][0]['message']['content']
        print('content len:', len(content))
        print('finish_reason:', d['choices'][0].get('finish_reason'))
        print('usage:', d.get('usage'))
        # check for JSON after </think>
        import re
        post_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        post_think = re.sub(r'<think>.*', '', post_think, flags=re.DOTALL)
        print('post-think len:', len(post_think.strip()))
        print('post-think first 500:', post_think.strip()[:500])
        print('content last 400:', content[-400:])

asyncio.run(test())
