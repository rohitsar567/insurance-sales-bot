#!/usr/bin/env python3
"""KI-138: Canonicalize policy_name across extracted JSONs and Chroma metadata.

Strategy:
  - For each `policy_id_base` (policy_id stripped of trailing `__<doctype>`),
    join extracted policy_name(s) against Chroma chunk policy_name(s).
  - When they disagree, treat the Chroma-majority name as the canonical label
    (Chroma names are short clean labels; extracted names often contain
    parenthetical descriptions or doctype suffixes).
  - Overwrite the extracted JSON `policy_name` and update every Chroma chunk's
    `policy_name` metadata in place.

Skip list (require human review — left untouched):
  - bajaj-allianz__group-health-guard-gold (Silver/Gold conflict)
  - reliance-general__hospi-care, __health-gain, __group-mediclaim (IndusInd co-brand)
  - regulatory__irda-grievance-redressal-handbook (irda→irdai slug rename)
  - regulatory__protection-of-policyholders-interests-2024 (slug rename)

Backup of Chroma sqlite was taken before running:
  rag/vectors/chroma.sqlite3.pre-ki138.bak

Run:
  /Users/rohitsar/.cache/uv-venvs/insurance-sales-bot/bin/python3 \
      tools/canonicalize_policy_names.py
"""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

import chromadb

REPO = '/Users/rohitsar/Developer/Insurance Sales Bot'
EXTRACTED_DIR = os.path.join(REPO, 'rag/_hf_dataset_backup/rag/extracted')
CHROMA_PATH = os.path.join(REPO, 'rag/vectors/')
MISMATCH_REPORT = '/tmp/policy_name_mismatches.json'

SKIP_BASES = {
    'bajaj-allianz__group-health-guard-gold',
    'reliance-general__hospi-care',
    'reliance-general__health-gain',
    'reliance-general__group-mediclaim',
    'regulatory__irda-grievance-redressal-handbook',
    'regulatory__protection-of-policyholders-interests-2024',
}


def policy_id_base(pid: str) -> str:
    parts = pid.split('__')
    return '__'.join(parts[:-1]) if len(parts) >= 3 else pid


def load_extracted() -> Dict[str, List[Tuple[str, str, str]]]:
    """base -> [(path, policy_name, policy_id), ...]"""
    out: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    for path in glob.glob(os.path.join(EXTRACTED_DIR, '*.json')):
        fname = os.path.basename(path)
        if fname.startswith('_'):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                d = json.load(fh)
        except Exception as exc:
            print(f'WARN unreadable extracted JSON {path}: {exc}', file=sys.stderr)
            continue
        pid = d.get('policy_id', fname.replace('.json', ''))
        pname = d.get('policy_name', '')
        out[policy_id_base(pid)].append((path, pname, pid))
    return out


def load_chroma(coll) -> Dict[str, List[Tuple[str, str, str]]]:
    """base -> [(chunk_id, policy_name, policy_id), ...]"""
    got = coll.get(include=['metadatas'])
    out: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    for cid, md in zip(got['ids'], got['metadatas']):
        pid = md.get('policy_id', '')
        pname = md.get('policy_name', '')
        out[policy_id_base(pid)].append((cid, pname, pid))
    return out


def build_mismatch_table(extracted, chroma) -> List[dict]:
    rows: List[dict] = []
    bases = set(extracted) | set(chroma)
    for base in sorted(bases):
        ext = extracted.get(base, [])
        chr_ = chroma.get(base, [])
        if not ext or not chr_:
            continue
        ext_names = [n for _, n, _ in ext]
        chr_names = [n for _, n, _ in chr_]
        if set(ext_names) == set(chr_names):
            continue
        chr_majority = Counter(chr_names).most_common(1)[0][0]
        rows.append({
            'policy_id_base': base,
            'extracted_name': Counter(ext_names).most_common(1)[0][0],
            'extracted_names_all': sorted(set(ext_names)),
            'chroma_name_majority': chr_majority,
            'chroma_names_all': sorted(set(chr_names)),
            'proposed_canonical': chr_majority,
            'extracted_files': [p for p, _, _ in ext],
            'chroma_doc_ids': [c for c, _, _ in chr_],
            'chroma_chunk_count': len(chr_),
        })
    return rows


def main() -> int:
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    coll = client.get_collection('policies')

    extracted = load_extracted()
    chroma = load_chroma(coll)

    rows = build_mismatch_table(extracted, chroma)
    with open(MISMATCH_REPORT, 'w', encoding='utf-8') as fh:
        json.dump(rows, fh, indent=2, ensure_ascii=False)
    print(f'mismatch table: {len(rows)} rows -> {MISMATCH_REPORT}')

    actionable = [r for r in rows if r['policy_id_base'] not in SKIP_BASES]
    skipped = [r for r in rows if r['policy_id_base'] in SKIP_BASES]
    print(f'actionable: {len(actionable)}  skipped (human review): {len(skipped)}')

    json_updates = 0
    json_missing = 0
    chroma_updates = 0
    samples = []

    for row in actionable:
        canonical = row['proposed_canonical']

        # 1) Update extracted JSON files
        for fpath in row['extracted_files']:
            if not os.path.exists(fpath):
                print(f'WARN missing extracted JSON: {fpath}', file=sys.stderr)
                json_missing += 1
                continue
            try:
                with open(fpath, 'r', encoding='utf-8') as fh:
                    d = json.load(fh)
            except Exception as exc:
                print(f'WARN unreadable {fpath}: {exc}', file=sys.stderr)
                continue
            before = d.get('policy_name', '')
            if before != canonical:
                d['policy_name'] = canonical
                with open(fpath, 'w', encoding='utf-8') as fh:
                    json.dump(d, fh, indent=2, ensure_ascii=False)
                json_updates += 1

        # 2) Update Chroma metadata for every chunk in this base
        ids_to_update: List[str] = []
        metas_to_update: List[dict] = []
        existing = coll.get(ids=row['chroma_doc_ids'], include=['metadatas'])
        for cid, md in zip(existing['ids'], existing['metadatas']):
            if md.get('policy_name') == canonical:
                continue
            new_md = dict(md)
            new_md['policy_name'] = canonical
            ids_to_update.append(cid)
            metas_to_update.append(new_md)

        if ids_to_update:
            # batch update for speed
            BATCH = 500
            for i in range(0, len(ids_to_update), BATCH):
                coll.update(
                    ids=ids_to_update[i:i + BATCH],
                    metadatas=metas_to_update[i:i + BATCH],
                )
            chroma_updates += len(ids_to_update)

        if len(samples) < 5:
            samples.append({
                'policy_id_base': row['policy_id_base'],
                'before_extracted': row['extracted_name'],
                'before_chroma_majority': row['chroma_name_majority'],
                'after': canonical,
            })

    print(f'JSON files updated: {json_updates} (missing: {json_missing})')
    print(f'Chroma chunks updated: {chroma_updates}')

    # 3) Verify by re-joining
    extracted2 = load_extracted()
    chroma2 = load_chroma(coll)
    remaining = build_mismatch_table(extracted2, chroma2)
    remaining_bases = [r['policy_id_base'] for r in remaining]
    print(f'mismatches_remaining: {len(remaining)}')
    for r in remaining:
        marker = 'SKIPPED' if r['policy_id_base'] in SKIP_BASES else 'UNEXPECTED'
        print(f"  [{marker}] {r['policy_id_base']}: ext={r['extracted_name']!r} chr={r['chroma_name_majority']!r}")

    print('\nsample before/after:')
    for s in samples:
        print(f"  {s['policy_id_base']}")
        print(f"    extracted-before: {s['before_extracted']}")
        print(f"    chroma-before:    {s['before_chroma_majority']}")
        print(f"    after (canonical):{s['after']}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
