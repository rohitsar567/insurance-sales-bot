"""List OpenRouter free models."""
import httpx
k = None
with open('.env') as f:
    for line in f:
        if line.startswith('OPENROUTER_API_KEY='):
            k = line.split('=',1)[1].strip(); break

r = httpx.get('https://openrouter.ai/api/v1/models', headers={'Authorization': f'Bearer {k}'}, timeout=30)
data = r.json()
print('total models:', len(data.get('data', [])))
# Find free
free = []
for m in data.get('data', []):
    p = m.get('pricing', {})
    pi = float(p.get('prompt', '0') or '0')
    pc = float(p.get('completion', '0') or '0')
    if pi == 0 and pc == 0:
        free.append((m['id'], m.get('context_length',0)))
print(f'\nfree models: {len(free)}')
for mid, cl in free[:50]:
    print(f'  {mid}  ctx={cl}')
