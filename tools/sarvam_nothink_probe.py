"""Try to disable Sarvam-M reasoning via various API params."""
import asyncio, httpx
from rag.extract import build_extract_prompt, schema_excerpt, read_full_text, EXTRACT_SYSTEM
from pathlib import Path

async def test():
    p = Path('rag/corpus/aditya-birla/activ-secure-cancer-secure__brochure.pdf')
    text = read_full_text(p)
    prompt = build_extract_prompt(text, schema_excerpt(), 'test')
    k = None
    with open('.env') as f:
        for line in f:
            if line.startswith('SARVAM_API_KEY='):
                k = line.split('=',1)[1].strip(); break

    # Add /no_think suffix per Qwen3-style reasoning toggle (Sarvam-M may inherit)
    sys_no_think = EXTRACT_SYSTEM + "\n\n/no_think"

    body = {
        'model': 'sarvam-m',
        'messages': [
            {'role':'system','content':sys_no_think},
            {'role':'user','content':prompt},
        ],
        'temperature': 0.0,
        'max_tokens': 2048,
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post('https://api.sarvam.ai/v1/chat/completions',
            headers={'api-subscription-key': k, 'Authorization': f'Bearer {k}','Content-Type':'application/json'},
            json=body)
        print('status:', r.status_code)
        d = r.json()
        content = d['choices'][0]['message']['content']
        print('content len:', len(content))
        print('finish_reason:', d['choices'][0].get('finish_reason'))
        print('usage:', d.get('usage'))
        import re
        no_think = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        no_think = re.sub(r'<think>.*', '', no_think, flags=re.DOTALL).strip()
        print('post-think len:', len(no_think))
        print('first 500:', no_think[:500])
        print('last 300:', no_think[-300:])

asyncio.run(test())
