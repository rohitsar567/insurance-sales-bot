import os, httpx, asyncio
async def test():
    k = None
    with open('.env') as f:
        for line in f:
            if line.startswith('GROQ_API_KEY='):
                k = line.split('=',1)[1].strip()
                break
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post('https://api.groq.com/openai/v1/chat/completions',
            headers={'Authorization': f'Bearer {k}', 'Content-Type': 'application/json'},
            json={'model': 'llama-3.3-70b-versatile',
                  'messages': [{'role':'user','content':'hi'}],
                  'max_tokens': 5})
        print('status:', r.status_code)
        print('rem-req:', r.headers.get('x-ratelimit-remaining-requests'))
        print('rem-tok:', r.headers.get('x-ratelimit-remaining-tokens'))
        print('reset-req:', r.headers.get('x-ratelimit-reset-requests'))
        print('reset-tok:', r.headers.get('x-ratelimit-reset-tokens'))
        print('retry-after:', r.headers.get('retry-after'))

asyncio.run(test())
