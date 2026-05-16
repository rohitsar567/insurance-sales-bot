#!/usr/bin/env python3
"""
Extract premium samples from rag/extracted/ for policies missing in
40-data/premiums/illustrative_premiums.json.

Strategy:
  - For each policy_facts slug not already in illustrative_premiums:
      - Look for premium_range_indicative_inr in extracted JSON(s)
      - Parse age/SI/floater hints from the key names
      - If found: add 1-2 sample entries with source_quality="brochure_extract"
      - Else: append to _pending_scrape.txt
"""
import json
import os
import re

ROOT = '/Users/rohitsar/Developer/Insurance Sales Bot'
PF_DIR = os.path.join(ROOT, '40-data/policy_facts')
EXT_DIR = os.path.join(ROOT, 'rag/extracted')
PREMIUMS_PATH = os.path.join(ROOT, '40-data/premiums/illustrative_premiums.json')
PENDING_PATH = os.path.join(ROOT, '40-data/premiums/_pending_scrape.txt')


def parse_key(key):
    """Parse keys like '26-45_SI_45L_deductible_5L_1A' or '35y_SI_5L_floater'.
    Returns dict with age, sum_insured_inr, family_size, deductible_inr, family_floater.
    Returns None if can't parse age or SI.
    """
    out = {
        'age': None,
        'sum_insured_inr': None,
        'family_size': 1,
        'deductible_inr': None,
        'is_floater': False,
    }
    # Age
    m = re.search(r'(\d{2})\s*[-y]\s*(\d{2})?', key)
    if m:
        if m.group(2):
            # range — take midpoint
            out['age'] = int((int(m.group(1)) + int(m.group(2))) / 2)
        else:
            out['age'] = int(m.group(1))

    # SI: e.g., "SI_5L" or "SI_45L"
    m = re.search(r'SI[_\s]*(\d+(?:\.\d+)?)\s*L', key, re.I)
    if m:
        out['sum_insured_inr'] = int(float(m.group(1)) * 100000)

    # Deductible
    m = re.search(r'deductible[_\s]*(\d+(?:\.\d+)?)\s*L', key, re.I)
    if m:
        out['deductible_inr'] = int(float(m.group(1)) * 100000)

    # Floater
    if 'floater' in key.lower():
        out['is_floater'] = True
        out['family_size'] = 2

    # Adult count (1A, 2A)
    m = re.search(r'(\d)A\b', key)
    if m:
        out['family_size'] = int(m.group(1))

    if out['age'] is None or out['sum_insured_inr'] is None:
        return None
    return out


def base_slug_from_filename(fname):
    """Strip .json + __wordings/__brochure/__cis suffix."""
    s = fname.replace('.json', '')
    for suf in ('__wordings', '__brochure', '__cis'):
        if s.endswith(suf):
            return s[:-len(suf)]
    return s


def main():
    pf_files = sorted([f for f in os.listdir(PF_DIR) if f.endswith('.json')])

    # Build map: base_slug -> list of associated extracted JSON files
    slug_to_extracted = {}
    for f in pf_files:
        base = base_slug_from_filename(f)
        ext_path = os.path.join(EXT_DIR, f)
        if os.path.exists(ext_path):
            slug_to_extracted.setdefault(base, []).append(ext_path)

    # Load existing premiums
    with open(PREMIUMS_PATH) as f:
        prem_data = json.load(f)
    existing_slugs = set(prem_data['base_premiums'].keys())

    # Build full universe of slugs (from pf_files)
    all_slugs = sorted(slug_to_extracted.keys())
    missing = [s for s in all_slugs if s not in existing_slugs]
    print(f'Total unique slugs: {len(all_slugs)}')
    print(f'Already have premiums: {len(existing_slugs)}')
    print(f'Missing: {len(missing)}')

    added = []
    pending = []

    for slug in missing:
        ext_paths = slug_to_extracted[slug]
        samples = []
        policy_name = None
        for ep in ext_paths:
            with open(ep) as f:
                d = json.load(f)
            if not policy_name:
                policy_name = d.get('policy_name')
            pr = d.get('premium_range_indicative_inr')
            if not pr:
                continue
            src_url = d.get('source_pdf_url') or ''
            src_pdf = d.get('source_pdf_path', '')
            if isinstance(pr, dict):
                for k, v in pr.items():
                    parsed = parse_key(k)
                    if not parsed:
                        continue
                    try:
                        prem_inr = int(float(v))
                    except (ValueError, TypeError):
                        continue
                    if prem_inr <= 0 or prem_inr > 5_000_000:
                        continue
                    samples.append({
                        'age': parsed['age'],
                        'sum_insured_inr': parsed['sum_insured_inr'],
                        'city_tier': 'metro',
                        'smoker': False,
                        'family_size': parsed['family_size'],
                        'annual_premium_inr': prem_inr,
                        'source_url': src_url or 'extracted_from_brochure',
                        'source_note': f'Found in {os.path.basename(ep).replace(".json","")} PDF: premium_range_indicative_inr["{k}"]={prem_inr}',
                        'source_quality': 'brochure_extract',
                    })
        if samples:
            # Limit to first 4 samples (don't bloat)
            samples = samples[:4]
            prem_data['base_premiums'][slug] = {
                'policy_id': slug.replace('__', '-'),
                'policy_name': policy_name or slug,
                'samples': samples,
            }
            added.append((slug, len(samples)))
        else:
            pending.append(slug)

    # Write premiums back
    with open(PREMIUMS_PATH, 'w') as f:
        json.dump(prem_data, f, indent=2, ensure_ascii=False)

    # Write pending scrape list
    with open(PENDING_PATH, 'w') as f:
        f.write('# Policies needing manual PolicyBazaar/InsuranceDekho premium scrape\n')
        f.write(f'# Generated: 2026-05-15  Count: {len(pending)}\n\n')
        for slug in pending:
            f.write(f'{slug}\n')

    print(f'\nAdded brochure-extracted premiums for: {len(added)} policies')
    for slug, n in added:
        print(f'  {slug}: {n} samples')
    print(f'\nPending manual scrape: {len(pending)} policies → {PENDING_PATH}')


if __name__ == '__main__':
    main()
