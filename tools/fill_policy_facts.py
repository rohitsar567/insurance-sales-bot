#!/usr/bin/env python3
"""
Fill structured fields in 40-data/policy_facts/*.json (Schema A skeletons)
from rag/extracted/*.json structured data.

Skips Schema B (already curated by sibling agent #210).
"""
import json
import os
import re
import sys

ROOT = '/Users/rohitsar/Developer/Insurance Sales Bot'
PF_DIR = os.path.join(ROOT, '40-data/policy_facts')
EXT_DIR = os.path.join(ROOT, 'rag/extracted')


def coerce_int(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    if isinstance(x, str):
        m = re.search(r'(\d+)', x.replace(',', ''))
        if m:
            return int(m.group(1))
    return None


def coerce_bool(x):
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ('true', 'yes', 'y', 'covered', 'available', '1'):
            return True
        if s in ('false', 'no', 'n', 'not covered', 'excluded', '0'):
            return False
    return None


def make_wrapper(value, unit, ext_data, note=None, confidence='high'):
    """Build the {value, unit, source_pdf_path, source_quote} wrapper."""
    pdf = ext_data.get('source_pdf_path', '')
    quote = note if note else 'extracted from PDF via LLM (rag/extracted) — see source for verbatim'
    return {
        'value': value,
        'unit': unit,
        'source_pdf_path': pdf,
        'source_quote': quote,
        '_confidence': confidence,
    }


def get_nested(ext_data, key, subkey):
    """Get ext_data[key][subkey] if exists, else None."""
    v = ext_data.get(key)
    if isinstance(v, dict):
        return v.get(subkey)
    return None


def parse_room_rent_pct(text):
    """Try to extract a percentage from room rent capping text. Returns int or None."""
    if not isinstance(text, str):
        return None
    # e.g. "Up to 1% of SI", "1% of sum insured"
    m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*of\s*(?:the\s*)?(?:sum insured|SI)', text, re.I)
    if m:
        return float(m.group(1))
    return None


def fill_one(pf_path, ext_path):
    """Merge extracted data into a skeleton policy_facts file. Returns dict of filled fields."""
    with open(pf_path) as f:
        pf = json.load(f)
    with open(ext_path) as f:
        ext = json.load(f)

    filled = {}

    def set_field(field_name, value, unit, note=None, confidence='high', add_if_missing=True):
        if value is None or value == '':
            return
        old = pf.get(field_name)
        if isinstance(old, dict):
            if old.get('value') in (None, '', []):
                old['value'] = value
                old['unit'] = unit
                old['source_pdf_path'] = ext.get('source_pdf_path', old.get('source_pdf_path', ''))
                if note:
                    old['source_quote'] = note
                old['_confidence'] = confidence
                filled[field_name] = value
        elif old is None:
            if add_if_missing:
                pf[field_name] = make_wrapper(value, unit, ext, note, confidence)
                filled[field_name] = value

    # ----- Direct mappings -----
    set_field('min_entry_age', coerce_int(ext.get('min_entry_age_years')), 'years',
              'min_entry_age_years from extracted PDF data')
    set_field('max_entry_age', coerce_int(ext.get('max_entry_age_years')), 'years',
              'max_entry_age_years from extracted PDF data')
    set_field('max_renewal_age', coerce_int(ext.get('max_renewal_age_years')), 'years',
              'max_renewal_age_years from extracted PDF data')
    set_field('min_child_entry_age', coerce_int(ext.get('min_child_entry_age_days')), 'days',
              'min_child_entry_age_days from extracted PDF data')

    # Sum insured options
    sio = ext.get('sum_insured_options_inr')
    if isinstance(sio, list) and sio:
        set_field('sum_insured_options', sio, 'INR',
                  'sum_insured_options_inr from extracted PDF data')

    # Waiting periods
    set_field('grace_period', coerce_int(ext.get('grace_period_days')), 'days',
              'grace_period_days from extracted PDF data')
    set_field('free_look_period', coerce_int(ext.get('free_look_period_days')), 'days',
              'free_look_period_days from extracted PDF data')
    set_field('initial_waiting_period_days', coerce_int(ext.get('initial_waiting_period_days')),
              'days', 'initial_waiting_period_days from extracted PDF data')
    set_field('pre_existing_disease_waiting_months',
              coerce_int(ext.get('pre_existing_disease_waiting_months')),
              'months', 'pre_existing_disease_waiting_months from extracted PDF data')
    set_field('specific_disease_waiting_months',
              coerce_int(ext.get('specific_disease_waiting_months')),
              'months', 'specific_disease_waiting_months from extracted PDF data')
    set_field('maternity_waiting_months',
              coerce_int(ext.get('maternity_waiting_months')),
              'months', 'maternity_waiting_months from extracted PDF data')

    # Hospitalization
    set_field('pre_hospitalization_days', coerce_int(ext.get('pre_hospitalization_days')),
              'days', 'pre_hospitalization_days from extracted PDF data')
    set_field('post_hospitalization_days', coerce_int(ext.get('post_hospitalization_days')),
              'days', 'post_hospitalization_days from extracted PDF data')

    # Day-care count — try to extract a number from limit_text or notes
    dc = ext.get('day_care_treatments')
    if isinstance(dc, dict):
        # Look for a number in limit_text or notes
        for fld in ('limit_text', 'notes'):
            txt = dc.get(fld, '')
            if isinstance(txt, str):
                m = re.search(r'(\d{2,4})\+?\s*(?:listed\s+)?day[\s\-]?care', txt, re.I)
                if not m:
                    m = re.search(r'(\d{2,4})\s+(?:procedures|treatments)', txt, re.I)
                if m:
                    n = int(m.group(1))
                    if 10 <= n <= 1000:
                        set_field('day_care_treatments_count', n, 'count',
                                  f'extracted from day_care_treatments.{fld}: "{txt[:120]}"',
                                  'medium')
                        break

    # Network hospital count — try structured field first, then regex over serialized JSON
    nhc = ext.get('network_hospital_count')
    nhc_int = coerce_int(nhc)
    if nhc_int and nhc_int >= 100:
        set_field('network_hospital_count', nhc_int, 'count',
                  'network_hospital_count from extracted PDF data')
    else:
        # Fallback regex
        full_text = json.dumps(ext)
        m = re.search(r'(\d{3,5})\+?\s*(?:network\s+)?hospitals?', full_text, re.I)
        if m:
            v = int(m.group(1))
            if 1000 <= v <= 50000:
                set_field('network_hospital_count', v, 'count',
                          f'regex extracted from serialized extracted JSON: matched "{m.group(0)[:80]}"',
                          'medium')

    # Day-care: also try regex fallback over whole JSON
    if not isinstance(pf.get('day_care_treatments_count'), dict) or \
       pf.get('day_care_treatments_count', {}).get('value') in (None, '', []):
        full_text = json.dumps(ext)
        for pat in (
            re.compile(r'(\d{2,4})\+?\s*(?:listed\s+)?day[\s\-]?care\s+(?:procedures?|treatments?)', re.I),
            re.compile(r'day[\s\-]?care\s+(?:procedures?|treatments?)[^\d]{0,40}?(\d{2,4})', re.I),
        ):
            m = pat.search(full_text)
            if m:
                v = int(m.group(1))
                if 50 <= v <= 1000:
                    set_field('day_care_treatments_count', v, 'count',
                              f'regex extracted from serialized JSON: "{m.group(0)[:80]}"',
                              'medium')
                    break

    # Co-payment percentage
    cop = ext.get('copayment_pct')
    cop_int = coerce_int(cop) if cop is not None else None
    if cop_int is not None and 0 <= cop_int <= 100:
        # In schema A the field is named co_payment_pct
        set_field('co_payment_pct', cop_int, '%',
                  f'copayment_pct={cop_int}% from extracted PDF data')

    # Room rent — try to extract % of SI; else fallback to verbatim
    rr = ext.get('room_rent_capping')
    if isinstance(rr, str) and rr.strip():
        pct = parse_room_rent_pct(rr)
        if pct is not None:
            set_field('room_rent_capped_at_pct_of_si', pct, '%',
                      f'parsed from room_rent_capping: "{rr[:150]}"', 'medium')
        # Always stash the verbatim room rent description (add field if missing)
        set_field('room_rent_capping', rr, 'text',
                  f'room_rent_capping (verbatim): "{rr[:200]}"', 'high')

    # NCB — add as new field if missing
    ncb = ext.get('no_claim_bonus_pct')
    ncb_int = coerce_int(ncb)
    if ncb_int is not None and 0 <= ncb_int <= 200:
        set_field('no_claim_bonus_pct', ncb_int, '%',
                  'no_claim_bonus_pct from extracted PDF data', 'high')

    # CSR
    csr = ext.get('claim_settlement_ratio_pct')
    csr_f = None
    try:
        csr_f = float(csr) if csr is not None else None
    except (ValueError, TypeError):
        csr_f = coerce_int(csr)
    if csr_f is not None and 0 < csr_f <= 100:
        set_field('claim_settlement_ratio_pct', csr_f, '%',
                  'claim_settlement_ratio_pct from extracted PDF data')

    # Coverage booleans + descriptions (add as new fields if missing in skeleton)
    for ext_key, pf_key, label in [
        ('ayush_coverage', 'ayush_coverage', 'AYUSH coverage'),
        ('maternity_coverage', 'maternity_coverage', 'Maternity coverage'),
        ('newborn_coverage', 'newborn_coverage', 'Newborn coverage'),
        ('organ_donor_expenses', 'organ_donor_expenses', 'Organ donor expenses'),
        ('restoration_benefit', 'restoration_benefit', 'Restoration benefit'),
        ('domiciliary_treatment', 'domiciliary_treatment', 'Domiciliary treatment'),
        ('worldwide_emergency_cover', 'worldwide_emergency_cover', 'Worldwide emergency cover'),
        ('preventive_health_checkup', 'preventive_health_checkup', 'Preventive health checkup'),
        ('critical_illness_cover', 'critical_illness_cover', 'Critical illness cover'),
    ]:
        ev = ext.get(ext_key)
        if isinstance(ev, dict):
            covered = ev.get('covered')
            note_parts = []
            if 'limit_text' in ev and ev['limit_text']:
                note_parts.append(f"limit: {ev['limit_text']}")
            if 'notes' in ev and ev['notes']:
                note_parts.append(ev['notes'])
            note_str = '; '.join(note_parts) if note_parts else f'{label} from extracted PDF data'
            if isinstance(covered, bool):
                set_field(pf_key, covered, 'boolean', note_str[:300], 'high')

    # Cashless treatment supported (top-level bool in extracted)
    cts = ext.get('cashless_treatment_supported')
    if isinstance(cts, bool):
        set_field('cashless_treatment_supported', cts, 'boolean',
                  'cashless_treatment_supported from extracted PDF data', 'high')

    # NCB cap percentage (in addition to no_claim_bonus_pct)
    ncb_cap = ext.get('no_claim_bonus_cap_pct')
    ncb_cap_int = coerce_int(ncb_cap)
    if ncb_cap_int is not None and 0 <= ncb_cap_int <= 200:
        set_field('no_claim_bonus_cap_pct', ncb_cap_int, '%',
                  'no_claim_bonus_cap_pct from extracted PDF data', 'high')

    # TAT cashless authorization
    tat = ext.get('tat_cashless_authorization_hours')
    tat_int = coerce_int(tat)
    if tat_int is not None and 0 < tat_int <= 72:
        set_field('tat_cashless_authorization_hours', tat_int, 'hours',
                  'tat_cashless_authorization_hours from extracted PDF data', 'high')

    # Geographic coverage
    geo = ext.get('geographic_coverage')
    if isinstance(geo, str) and geo.strip():
        set_field('geographic_coverage', geo, 'enum',
                  f'geographic_coverage from extracted PDF data: "{geo}"', 'high')

    # Policy type (indemnity / fixed-benefit / etc)
    pt = ext.get('policy_type')
    if isinstance(pt, str) and pt.strip():
        set_field('policy_type_indemnity_or_fixed', pt, 'enum',
                  f'policy_type from extracted PDF data: "{pt}"', 'high')

    # Deductible amount
    dam = ext.get('deductible_amount_inr')
    dam_int = coerce_int(dam)
    if dam_int is not None and dam_int > 0:
        set_field('deductible_amount', dam_int, 'INR',
                  'deductible_amount_inr from extracted PDF data', 'high')

    # Modern treatments boolean
    mt = ext.get('modern_treatments')
    if isinstance(mt, dict):
        mc = mt.get('covered')
        if isinstance(mc, bool):
            set_field('modern_treatments_covered', mc, 'boolean',
                      mt.get('notes', 'modern_treatments from extracted PDF data')[:300], 'high')

    # Policy term
    ppt = ext.get('premium_payment_term_years')
    if isinstance(ppt, list) and ppt:
        set_field('policy_term_options_years', ppt, 'years',
                  'premium_payment_term_years from extracted PDF data')
    elif isinstance(ppt, (int, str)):
        v = coerce_int(ppt)
        if v:
            set_field('policy_term_options_years', [v], 'years',
                      'premium_payment_term_years from extracted PDF data')

    return pf, filled


def main():
    pf_files = sorted([f for f in os.listdir(PF_DIR) if f.endswith('.json')])
    skipped_schema_b = 0
    skipped_no_ext = 0
    processed = 0
    total_filled = {}
    log_lines = []

    for fname in pf_files:
        pf_path = os.path.join(PF_DIR, fname)
        with open(pf_path) as f:
            pf = json.load(f)
        # Skip Schema B (already curated)
        if 'co_payment_pct' not in pf:
            skipped_schema_b += 1
            continue
        ext_path = os.path.join(EXT_DIR, fname)
        if not os.path.exists(ext_path):
            skipped_no_ext += 1
            log_lines.append(f'SKIP no_ext: {fname}')
            continue

        new_pf, filled = fill_one(pf_path, ext_path)
        # Write back
        with open(pf_path, 'w') as f:
            json.dump(new_pf, f, indent=2, ensure_ascii=False)
        processed += 1
        for k in filled:
            total_filled[k] = total_filled.get(k, 0) + 1

    print(f'Processed: {processed}')
    print(f'Skipped (already curated, Schema B): {skipped_schema_b}')
    print(f'Skipped (no extracted source): {skipped_no_ext}')
    print()
    print('Fields filled (count of files):')
    for k in sorted(total_filled, key=lambda x: -total_filled[x]):
        print(f'  {k:50s}  {total_filled[k]}')


if __name__ == '__main__':
    main()
