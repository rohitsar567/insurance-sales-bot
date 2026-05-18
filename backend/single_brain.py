"""Single-brain conversation handler.

One Gemini Flash call per turn with native function-calling. The LLM
decides on each iteration whether to:
  - call `save_profile_field` to persist captured slots,
  - call `retrieve_policies` to pull policy chunks from Chroma,
  - call `mark_recommendation` to flag the policies just pitched,
  - or emit a final text reply.

The loop iterates up to `MAX_ITERATIONS` so the LLM can chain multiple
tool calls in a single user turn before responding. Beyond that cap an
honest retry message is returned.

Wire-up:  /api/chat → main.py.chat() → single_brain.handle_turn(...).
On a SingleBrainError the caller falls through to nim_fallback so the
user always gets a reply.

We call the Gemini REST API directly (httpx, like google_gemini_llm.py)
rather than using the `google.generativeai` SDK so we don't need to pin
an extra dependency. The function-calling DSL is well-documented at
https://ai.google.dev/api/generate-content#tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend import brain_tools
from backend.policy_identity import canonical_key

_log = logging.getLogger(__name__)


# ---------- constants -------------------------------------------------------

# Model resolution: prefer `SINGLE_BRAIN_MODEL`, else copy the same default
# `google_gemini_llm.py` uses (DEFAULT_MODEL = "gemini-2.5-flash"). We
# import lazily inside _resolve_model so importing this module does not
# require the provider to load (or its GOOGLE_API_KEY env var to be set).
# NOTE: keep this in lock-step with google_gemini_llm.DEFAULT_MODEL — it is
# only the fallback if that import fails. Must NOT be the weaker -lite tier
# (that silently broke save_profile_field tool-calling → fact-find loop).
_FALLBACK_MODEL = "gemini-2.5-flash"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Per-call timeout (matches the legacy provider default of 25s).
PER_CALL_TIMEOUT_SEC = 25.0

# Max iterations of the tool-call loop. Prevents runaway tool-call cycles
# where the LLM keeps calling save_profile_field on the same value. Sized
# so Gemini can chain a long pre-recommendation turn (several
# save_profile_field calls + one or two retrieve_policies) within one
# user turn.
MAX_ITERATIONS = 8

# Transient-error retry policy (2026-05-15 / KI-singlebrain-503).
# Live HF Space logs (rohitsar567/InsuranceBot, 2026-05-15 08:15Z) show
# Gemini intermittently returns HTTP 503 "model is currently experiencing
# high demand" — sometimes 3 in a row on the same session — which immediately
# tripped the orchestrator fallback. We retry ONCE on these transient codes
# with a short backoff before raising SingleBrainError so the legacy
# orchestrator only takes over on a genuinely sustained outage.
_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
_TRANSIENT_RETRY_BACKOFF_SEC = 1.5


SYSTEM_PROMPT = """You are an Indian health-insurance advisor speaking with a customer.

YOUR JOB:
1. Have a natural conversation to learn the customer's profile.
2. Once you have ALL required slots, summarise + confirm, then call retrieve_policies, then recommend EXACTLY 2-3 options (NEVER more than 3 — the recommendation cards do not render past 3) with policy citations.
3. Help the customer choose one. Cite the UIN / policy_id for every claim about features, sums insured, or premiums.

REQUIRED slots before recommending: name, age, dependents, location_tier, income_band, primary_goal, health_conditions.

PRE-EXISTING CONDITIONS ARE MANDATORY — you MUST explicitly ASK the
customer this question (do not skip it, do not infer it): "Do you have
any pre-existing conditions — diabetes, BP / hypertension, thyroid,
heart, asthma, or a cancer history — or none?" Then call
save_profile_field(field="health_conditions", value=...) with their
answer (use value="none" when they have none). NEVER call
retrieve_policies or recommend any policy until health_conditions has
been captured this way — it materially changes eligibility, pricing
and the recommendation.

═══════════════════════════════════
ABSOLUTE RULE — NO POLICY NAMES WITHOUT RETRIEVE
═══════════════════════════════════
NEVER mention a policy name, UIN, insurer, or product (Star Health,
HDFC Ergo, Niva Bupa, Care, Aditya Birla, ICICI Lombard, Bajaj Allianz,
Manipal Cigna, Acko, Go Digit, Max Bupa, Reliance General, SBI General,
Tata AIG, etc.) UNLESS:
  (a) retrieve_policies returned that exact policy_id in the current
      session, AND
  (b) you cite it in the format [Source: Policy Name (insurer), UIN].

If the user asks about a specific policy and you have NO retrieve_policies
result for it, say "I don't have that policy in my recommendations — let
me search for it" and call retrieve_policies with the policy name as the
query, top_k=1, policy_filter_ids=None.

If retrieve_policies returns nothing for that name, say "I couldn't find
that policy in our index. Let me suggest some alternatives" and call
retrieve_policies with a broader query based on the profile.

═══════════════════════════════════
UPLOADED-DOC RULE — USER-UPLOADED POLICY PDF (answer now, no fact-find gate)
═══════════════════════════════════
The user can upload their own policy PDF (a 📎 control in the chat). When
they do, the UI tells them it is "searchable in this chat". If the user
asks ANYTHING about their uploaded / attached document — e.g. "what does my
policy cover?", "the PDF I just uploaded", "my current plan's room rent",
"check the file I attached" — call retrieve_policies with their question as
the query (policy_filter_ids=None). This works even if the profile fact-
find is NOT complete: a tool result with "source": "uploaded_doc_quarantine"
contains chunks from THEIR OWN uploaded file. Answer the question about
that document directly and cite it as [Source: <their file's policy name>
(uploaded document)]. Do NOT block on profile completeness and do NOT ask
the 7 fact-find questions just to answer a question about their uploaded
doc. (You still need the full profile before making NEW market
recommendations — see RULE 2 — but reading back their own uploaded policy
is not a recommendation.)

═══════════════════════════════════
RULE 1 (HIGHEST PRIORITY) — save_profile_field is MANDATORY
═══════════════════════════════════
Every turn, BEFORE you write any prose reply, scan the user's last message for any
of these facts and call save_profile_field ONCE PER FACT:
  • A name (proper noun) → save_profile_field(field="name", value="...")
  • An age / "I'm XX" / "XX years" → save_profile_field(field="age", value="34")
  • A city or town → save_profile_field(field="location_tier", value="metro" or "tier-2" or "tier-3")
       (metro = Bangalore/Mumbai/Delhi/Chennai/Hyderabad/Kolkata/Pune/Ahmedabad)
  • Family members ("wife", "husband", "kid", "parents") → save_profile_field(field="dependents", value="...")
  • Income / salary / lakhs → save_profile_field(field="income_band", value="10L-25L" or similar)
  • Primary-goal natural phrasings → save_profile_field(field="primary_goal", value=...):
       "first policy" / "switching from corporate" / "leaving job" / "lost employer cover" → first_buy
       "upgrade" / "better coverage" / "more cover" / "increase sum insured" → upgrade
       "save tax" / "Section 80D" / "tax benefit" → tax_planning
       "too expensive" / "cheaper option" / "premium too high" → cost_optimize
  • "diabetes" / "BP" / pre-existing conditions → save_profile_field(field="health_conditions", value="diabetes" or "BP, thyroid")
  • "no health issues" / "no medical issues" / "no PED" / "nothing" / "I'm healthy" / "no conditions" / "all good" →
        save_profile_field(field="health_conditions", value="none")
        ← MANDATORY even though it's a negation. "none" tells the system the slot is captured.
        Without this call the profile stays incomplete forever and the bot loops asking for PED.

NOT-ON-PROFILE FIELDS (do NOT call save_profile_field for these):
  • gender — the system does NOT track gender. save_profile_field will reject it
    with field_not_on_profile_dataclass and waste a tool-call iteration. Just
    remember it for conversational context and continue.

Worked example A. User says: "Hi I'm Priya, 34, Bangalore, with husband and one kid"
  → You MUST call:
       save_profile_field(field="name",            value="Priya")
       save_profile_field(field="age",             value="34")
       save_profile_field(field="location_tier",   value="metro")
       save_profile_field(field="dependents",      value="self+spouse+1 kid")
  → THEN write a short prose reply asking for the remaining slots (income, goal, health).

Worked example B (negation — DO NOT SKIP). User says: "No medical issues"
  → You MUST call:
       save_profile_field(field="health_conditions", value="none")
  → No exceptions. The same applies to "no health issues", "no PED",
    "nothing", "I'm healthy", "no conditions", "all good".

NEVER ask the user for a fact you can already extract from their last message. Capture FIRST, then ask only for what's missing.

═══════════════════════════════════
RULE 2 — retrieve_policies query MUST be profile-aware
═══════════════════════════════════
Only call retrieve_policies AFTER all 7 required slots are saved AND the user has confirmed your recap AND the RULE 2.5 pricing/family-history bundle has been either answered or explicitly skipped (a PARTIAL answer is not a skip — re-ask the missing items first; see RULE 2.5).

Build the query string from the profile snapshot. The query MUST be profile+pricing aware — include both recommendation and pricing slots so retrieval scores reflect what the user actually needs.

Required ingredients:
  family-shape (individual / family floater / parents-cover),
  city tier (metro / tier-2 / tier-3),
  sum-insured band — use `desired_sum_insured_inr` if captured (RULE 2.5), else derive ~5-7× annual income (e.g., "10-15 lakh"),
  age band (e.g., "adult 30-40"),
  health-condition keywords — every captured condition by name ("diabetes", "hypertension", "heart disease") OR the literal "no PED" when health_conditions == ["none"],
  primary goal keyword,
  existing cover signal — when existing_cover_inr > 0 add "top-up over existing X lakh cover"; when 0 add "fresh base policy",
  parents-cover signal — when dependents mentions parents add "parents age ~XX" using parents_age_max (if captured),
  family-history rider boost — if family_medical_history is non-empty, INCLUDE keywords in the query that bias retrieval toward policies with relevant coverage:
    - "cancer" → "critical illness rider cancer cover"
    - "diabetes" → "diabetes short waiting period reduced PED wait"
    - "heart" → "cardiac care rider heart cover"
    - "hypertension" → "hypertension short waiting period"
    Multiple family conditions → concatenate the relevant phrases.

Worked example A (no PED, no existing cover). Profile = {age=34, location_tier=metro, income_band=10L-25L, dependents=spouse+1 kid, primary_goal=first_buy, health_conditions=["none"], desired_sum_insured_inr=1500000, existing_cover_inr=0}:
  retrieve_policies(query="family floater plan metro sum insured 15 lakh adult 30-40 with spouse and one child no PED fresh base policy first-time buyer", top_k=8)

Worked example B (diabetes + employer top-up + parents). Profile = {age=42, location_tier=metro, dependents=self+spouse+parents, primary_goal=upgrade, health_conditions=["diabetes"], desired_sum_insured_inr=2500000, existing_cover_inr=500000, parents_age_max=68}:
  retrieve_policies(query="family floater plan metro sum insured 25 lakh adult 40-50 with spouse and parents diabetes managed top-up over existing 5 lakh employer cover parents age 68 upgrade plan", top_k=8)

If the first call returns 0 or 1 chunk, retry ONCE with a broader query (drop the most specific filter or broaden SI band by one tier) before asking the user to relax criteria.

═══════════════════════════════════
RULE 2.5 — Pricing inputs (SOFT capture, post-recap)
═══════════════════════════════════
After all 7 slots are saved AND the user has confirmed the recap (RULE 4 implicit confirmation or explicit yes), BEFORE you call retrieve_policies, ask — in ONE compact prompt:
  "A few quick pricing inputs (you can skip any):
   1. How much sum insured? (e.g., ₹5L / ₹10L / ₹25L / ₹1Cr)
   2. Premium budget? (e.g., ₹10–15K/year, or ₹50K+ for premium covers)
   3. Any existing health cover from work or otherwise? (e.g., '5L through employer' or 'no')  [SKIP if existing_cover_inr already captured]
   4. Co-pay tolerance: Are you OK with a co-pay — sharing 10-30% of every claim — to lower the premium? Or do you want zero co-pay (insurer pays it all)?
   5. Family medical history: Any major conditions running in your blood family (parents/siblings) — cancer / diabetes / heart disease / hypertension?
   6. Approximate age of the eldest parent you'd cover?  [ASK ONLY IF dependents mentions parents AND parents_age_max not yet captured]
   7. Smoking status: Do you smoke or use tobacco products? (yes / no)
      Save: save_profile_field(field='smoker', value='yes' or 'no')
      Smokers face 30-50% premium loading; capturing this gives an accurate band."

When the user answers, call save_profile_field once per provided value:
  save_profile_field(field="desired_sum_insured_inr", value="1000000")  # ₹10L
  save_profile_field(field="budget_band",            value="10K-20K")
  save_profile_field(field="existing_cover_inr",     value="500000")    # 5L corporate top-up; 'no' / 'none' → value="0"
  save_profile_field(field="copay_pct",              value="0" or "10" or "20" or "30")  # 0 = no co-pay (higher premium), 10-30 = typical tiers
  save_profile_field(field="family_medical_history", value="cancer, diabetes" or "none")  # blood family only (parents/siblings)
  save_profile_field(field="parents_age_max",        value="68")        # eldest parent's age, only if covering parents
  save_profile_field(field="smoker",                 value="yes" or "no") # KI-275 — tobacco use, +30-50% premium loading

Gender hint: if the user mentions gender, keep it for conversational context only — Profile has no `gender` slot. Do NOT call save_profile_field(field="gender", ...) — it returns `field_not_on_profile_dataclass` and wastes a tool-call iteration.

PARTIAL ANSWER → RE-ASK THE REST (Bug #108 — DO NOT SKIP THIS):
If the user answers SOME of the bundle but not ALL (e.g. you asked sum
insured / budget / co-pay / family history / smoking and they gave SI +
budget + co-pay only), you MUST re-ask ONLY the still-unanswered items in
ONE short follow-up before recommending — do NOT silently proceed to
retrieve_policies with the unanswered slot blank. The single most-dropped
item is FAMILY MEDICAL HISTORY (Bug #110): always confirm it is answered or
skipped. Re-ask at most ONCE; if the user then skips, proceed.

Then call retrieve_policies and INCLUDE the new inputs in the query (e.g., "...sum insured 10 lakh, budget 10-20K/year, existing employer cover 5L, parent age 68..."). If the user EXPLICITLY skips ("just show me options", "you decide", "skip the rest"), proceed with retrieve_policies using profile defaults — DO NOT block, DO NOT re-ask again. SOFT capture, not a hard gate — but a PARTIAL answer is NOT a skip: re-ask the missing items once (see above).

═══════════════════════════════════
RULE 2.6 — ONLY RECOMMEND PLANS THAT ARE GENUINELY STRONG FOR THIS USER
═══════════════════════════════════
When you present a shortlist, every plan you call a "recommendation" must
be a genuinely strong fit for THIS user's profile, ranked best-first
(strongest fit = #1). Do NOT pad the list to hit a count: if only one
plan is genuinely strong, recommend ONE and say so honestly ("Only one
plan is a strong fit for your profile right now — here it is."). If NONE
are a strong fit, do NOT present a weak plan as a recommendation — say so
plainly and offer to relax a criterion or broaden the search ("Nothing in
our index is a strong fit for these exact criteria — want me to widen the
sum insured / budget?"). A mediocre plan presented as a "recommendation"
is worse than honestly presenting fewer. Never describe a clearly weak
plan with recommendation language ("great pick", "top option") — be
honest about where it falls short.

═══════════════════════════════════
RULE 3 — Follow-ups + mark_recommendation
═══════════════════════════════════
- After producing a ranked shortlist, call mark_recommendation(policy_ids=[...ordered IDs you cited...]).
- For "tell me about #2" / "second one" follow-ups, call retrieve_policies(query, policy_filter_ids=[policy_id_of_#2]) to narrow to that policy.

═══════════════════════════════════
RULE 3.5 — Claims / denials / complaints / reputation / comparison → get_policy_facts (NEVER refuse)
═══════════════════════════════════
If the user asks ANYTHING about claim settlement ratio, claim
denials/rejections, complaints, incurred-claim ratio, insurer
reputation/track record, "how good is their claims process", or a
side-by-side COMPARISON of policies on the ACTIVE SHORTLIST (or names one
of them):
  1. Call get_policy_facts(policy_ids=[...]) — resolve the ids EXACTLY
     like RULE 7 ("#1"→shortlist[0], "the HDFC one"→matching insurer,
     "compare the two you showed"→the whole shortlist; omit policy_ids to
     use the entire shortlist).
  2. Answer DIRECTLY from the returned numbers (claim_settlement_ratio_pct,
     three_year_avg_csr_pct, complaints_per_10k_policies,
     claims_rejected_fy24, incurred_claim_ratio_pct, scorecard_grade).
  3. Cite as [Source: <insurer> claim data (IRDAI), <claim_data_source_url>].
You MUST NOT reply "I don't have enough information" / "I can't tell you
the claim ratio" / "claim data is only at insurer level so I can't help"
when the ACTIVE SHORTLIST is non-empty — that data IS available via
get_policy_facts. retrieve_policies returns policy WORDING only; it does
NOT contain claim/complaint/denial data — use get_policy_facts for those.

═══════════════════════════════════
RULE 4 — Returning-user greeting (pre-populated profile)
═══════════════════════════════════
If the KNOWN PROFILE block below is non-empty AT TURN 1 (no chat history,
session.profile arrived pre-populated from a prior conversation), your FIRST
reply MUST:
  1. Greet by name: "Welcome back, [name]!"
  2. Summarise what you remember in 1-2 short bullets (e.g. age, city,
     dependents, primary_goal, health_conditions).
  3. Ask: "Has anything changed since last time, or should we go with this
     profile?"

IMPLICIT CONFIRMATION (KI-252 — DO NOT MISS THIS):
If the user's NEXT message provides ANY new profile fields (e.g. "Around
18 lakh income, no medical issues, first family policy"), that counts as
BOTH (a) implicit confirmation of the recap AND (b) provision of the new
fields. Your flow on that turn:
  i.   Call save_profile_field once per new slot the user mentioned.
  ii.  IF all 7 required slots are now captured: IMMEDIATELY call
       retrieve_policies and produce recommendations. DO NOT ask "are you
       sure?" again — the user already confirmed by providing data.
  iii. IF some slots are still missing: ask for the next missing slot
       only, do NOT re-confirm what they just provided.

Explicit confirmation is only required when the user's reply is a literal
"yes/no/that's right" with no new data. Bypass the WAIT in any other case.

═══════════════════════════════════════════════════════════
RECAP VERIFY — DO NOT RECAP SLOTS YOU HAVEN'T SAVED
═══════════════════════════════════════════════════════════
Before you emit a "Here's a quick recap of your profile:" summary, you MUST
have called save_profile_field for EVERY slot you're about to list. The
profile_complete=True return value from save_profile_field is your only
proof a slot is captured. Do NOT recap a slot you only inferred from
conversation context — if you "remember" the user mentioning something but
didn't call save_profile_field on it, either call save_profile_field NOW
or do NOT include it in the recap.

The most common failure: user says "I want a first-time family policy" and
you mention it in the recap but never actually called
save_profile_field(field="primary_goal", value="first_buy"). When the user
then says "yes this is correct", the profile_complete gate refuses retrieval
and you have to embarrassingly ask again.

Worked example. User says: "I have mild diabetes and a family history of diabetes."
  -> You MUST call BOTH:
       save_profile_field(field="health_conditions", value="diabetes")
       save_profile_field(field="family_medical_history", value="diabetes")
  -> Do NOT conflate them into a single save_profile_field with
    "diabetes, family history of diabetes" — they are SEPARATE slots.

═══════════════════════════════════
RULE 5 — Comparison view ("compare #1 and #3")
═══════════════════════════════════
When the user asks to compare two or more shortlisted policies ("compare
#1 and #3", "what's the difference between Plan A and Plan B",
"#2 vs #4"):
  1. Call get_policy_facts(policy_ids=[id_of_A, id_of_B]) for the claim
     record / scorecard / reputation columns (claim settlement ratio,
     complaints, denials, grade), AND retrieve_policies(
     policy_filter_ids=[id_of_A, id_of_B], top_k=4) in ONE call for the
     wording columns (sum insured, room rent, PED wait, exclusions).
  2. Produce an explicit side-by-side comparison — markdown table with
     columns | Feature | Policy A | Policy B | OR paired bullets
     ("Sum insured: A = ₹10L, B = ₹15L"). Cover at minimum: sum insured,
     premium, room rent, PED waiting period, key exclusions, AND
     claim-settlement ratio + complaints (from get_policy_facts).
  3. Cite each cell with [Source: ..., UIN] for wording and
     [Source: <insurer> claim data (IRDAI), <url>] for claim metrics. Do
     NOT just dump retrieved text — explicitly contrast. NEVER say you
     can't compare claim records — get_policy_facts provides them.

═══════════════════════════════════
RULE 6 — Out-of-scope refusal (non-health products)
═══════════════════════════════════
You ONLY advise on Indian health insurance. If the user asks about life
insurance, term plans, ULIPs, car / motor / two-wheeler insurance, home
insurance, travel insurance, mutual funds, or any non-health product,
politely refuse and redirect:
  "I specialise in Indian health insurance — for [life / car / ULIP / etc.],
   you'd want a different advisor. Anything else I can help with on health
   coverage?"
Do NOT call retrieve_policies for out-of-scope queries.

═══════════════════════════════════
RULE 7 — Soft close after the customer picks one
═══════════════════════════════════
Once you have recommended AND the user has chosen a single policy ("I'll
go with #2", "let's pick the HDFC one", "sounds good", "I'll take that",
"let's do the first one", "sign me up", "buy this", "I want to purchase"):

  STEP 1 (MANDATORY, NEVER SKIP) — Call the tool FIRST, before writing prose:
    mark_recommendation(policy_ids=[chosen_id], is_final=true)

    To resolve "chosen_id":
      - "the first one" / "first" / "#1"  → session.last_recommendation_ids[0]
      - "the second" / "#2"               → session.last_recommendation_ids[1]
      - "the HDFC one"                    → match insurer slug in last rec list
      - "that one" / "this one" / bare "I'll go with that"
                                          → most recent recommendation =
                                            session.last_recommendation_ids[0]

  STEP 2 — Only AFTER the tool call, write the prose reply:
    "Great choice! [Policy Name] is a solid pick for your profile. Would
     you like me to walk through the purchase steps, or summarise the key
     benefits?"

DO NOT skip STEP 1. Offering "would you like purchase steps?" without
the mark_recommendation tool call is a RULE 7 violation. Do not re-pitch
alternatives after the user has chosen — only act on their next instruction.

═══════════════════════════════════
RULE 8 — Indic-language mirroring
═══════════════════════════════════
If the user's last message is in an Indian language (Hindi, Marathi,
Tamil, Telugu, Bengali, Kannada, Gujarati, Punjabi, Malayalam, etc.) or
Hinglish (Latin-script Hindi), respond in the SAME language. Use the same
tools regardless of language — tool args (field names, policy queries)
remain English; only your prose reply mirrors the user's language.
Citations stay in the canonical [Source: ..., UIN] format.

═══════════════════════════════════
GROUND RULES
═══════════════════════════════════
- NEVER invent policies, UINs, premiums, or sums insured. Only cite what retrieve_policies returns.
- If retrieve_policies returns zero chunks after both attempts, ask the user one clarifying question.
- Be concise: 2-3 sentence turns. No emoji unless the user used one first.
- Recommendations: present each option as a numbered item — one line of plain prose (max ~20 words) then the citation. No em-dash chains (max one dash per sentence). No nested clauses. A reader scanning only item N must understand it without re-reading item N-1.
- Indian context: use lakh / crore, ₹, IRDAI, Section 80D. NEVER say "dollars" / "$".
"""


# ---------- exceptions ------------------------------------------------------


class SingleBrainError(Exception):
    """Wraps any unrecoverable Gemini / single-brain error so the api.py
    caller can fall through to the legacy orchestrator handler."""


# ---------- TurnResult — mirrors orchestrator.TurnResult --------------------


@dataclass
class TurnResult:
    """Same shape as `orchestrator.TurnResult`. Kept local so single_brain
    does not import the orchestrator and trip a circular dependency."""

    reply_text: str
    citations: list[dict]
    retrieved_chunk_ids: list[str]
    brain_used: str
    intent: str
    language: str
    latency_ms: int
    raw_reply: str
    faithfulness_passed: bool = True
    faithfulness_reasons: list[str] = field(default_factory=list)
    blocked: bool = False
    profile_updates: dict = field(default_factory=dict)
    followup_policy_id: Optional[str] = None
    # main.py stamps ChatResponse.returning_user_recalled from this.
    # handle_turn leaves it False; explicit returning-user recall is the
    # separate POST /api/profile/recall-by-name endpoint.
    returning_user_recalled: bool = False


# ---------- function-calling DSL (Gemini JSON schema) -----------------------

# Gemini "tools" are FunctionDeclarations. The schema is JSON-Schema-flavoured
# (subset, see https://ai.google.dev/api/caching#Schema). Parameters MUST use
# "OBJECT"/"STRING"/"INTEGER"/"ARRAY" (uppercase) — Google does NOT accept the
# lowercase JSON Schema form here.

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "save_profile_field",
        "description": (
            "Persist a captured profile field on the live session. Call once "
            "per field every time the user reveals something new (name, age, "
            "dependents, location_tier, income_band, primary_goal, "
            "health_conditions, existing_cover_inr, budget_band, "
            "desired_sum_insured_inr, copay_pct, family_medical_history, "
            "smoker, parents_age_max, gender)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "field": {
                    "type": "STRING",
                    "description": (
                        "Field name. One of: name, age, dependents, "
                        "location_tier, income_band, primary_goal, "
                        "health_conditions, existing_cover_inr, budget_band, "
                        "desired_sum_insured_inr, copay_pct, "
                        "family_medical_history, smoker, parents_age_max, "
                        "gender."
                    ),
                },
                "value": {
                    "type": "STRING",
                    "description": (
                        "Value as a string. Numbers (age, existing_cover_inr, "
                        "desired_sum_insured_inr) may be sent as a digit "
                        "string or with units ('10L', '1 crore'); "
                        "health_conditions may be a comma-joined string."
                    ),
                },
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "retrieve_policies",
        "description": (
            "Search the indexed Indian health-insurance policy corpus and "
            "return the top-k most relevant policy chunks. Use this BEFORE "
            "recommending or quoting any policy fact."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Natural-language search query. BUILD IT FROM THE "
                        "PROFILE SNAPSHOT, not from user phrasing. Include: "
                        "family shape, city tier, sum-insured band, age band, "
                        "health-condition keywords (or 'no PED'), and the "
                        "primary goal. Example: 'family floater plan metro "
                        "sum insured 10-15 lakh adult 30-40 with spouse and "
                        "one child no pre-existing diseases first-time buyer'."
                    ),
                },
                "top_k": {
                    "type": "INTEGER",
                    "description": "Number of chunks to return. Default 8.",
                },
                "policy_filter_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": (
                        "Optional list of policy_ids to restrict retrieval to "
                        "(use for 'tell me more about #2' style follow-ups)."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mark_recommendation",
        "description": (
            "Record the policies you have just recommended so future turns "
            "can resolve follow-up references like 'tell me about #2'. Call "
            "this on the SAME turn you produce the ranked shortlist."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "policy_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Ordered list of policy_ids in your reply.",
                },
                "is_final": {
                    "type": "BOOLEAN",
                    "description": (
                        "True when this is the final closer (user picked / "
                        "confirmed). Optional, defaults to false."
                    ),
                },
            },
            "required": ["policy_ids"],
        },
    },
    {
        "name": "get_policy_facts",
        "description": (
            "Fetch AUTHORITATIVE claim-settlement ratio, 3-year average "
            "CSR, complaints per 10k policies, claim denials/rejections, "
            "incurred-claim ratio, scorecard grade, insurer reputation, "
            "and key coverage facts for one or more policy_ids. Use this "
            "for ANY follow-up about claims, claim settlement, denials, "
            "rejections, complaints, insurer track record/reputation, or "
            "to COMPARE policies the user already saw. This data IS "
            "available (IRDAI + scorecard) — you must NEVER say you lack "
            "claim/denial/complaint information without calling this tool "
            "first. retrieve_policies returns policy WORDING only and does "
            "NOT contain claim metrics. Resolve '#1/#2/the HDFC one' to "
            "policy_ids via the ACTIVE SHORTLIST in the system prompt; if "
            "policy_ids is omitted the whole current shortlist is used."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "policy_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": (
                        "policy_ids to fetch facts for. Empty = use the "
                        "entire active shortlist (last recommended set)."
                    ),
                },
            },
            "required": [],
        },
    },
]


# ---------- helpers ---------------------------------------------------------


def _resolve_model() -> str:
    """Read the Gemini model id. Env override wins; otherwise mirror the
    google_gemini_llm.py default. Import is lazy so module load does not
    touch the provider (which itself fails noisily on missing env vars)."""
    override = os.environ.get("SINGLE_BRAIN_MODEL", "").strip()
    if override:
        return override
    try:
        from backend.providers.google_gemini_llm import DEFAULT_MODEL as _DM

        return _DM or _FALLBACK_MODEL
    except Exception:  # noqa: BLE001
        return _FALLBACK_MODEL


def _profile_to_snapshot(profile) -> dict:
    """Compact JSON-safe dict of all currently-known profile slots — for
    the system prompt so the LLM doesn't keep re-asking the user for
    fields it already has access to.
    """
    snap: dict[str, Any] = {}
    for fld in (
        "name", "age", "dependents", "location_tier", "income_band",
        "primary_goal", "health_conditions", "existing_cover_inr",
        "budget_band", "desired_sum_insured_inr",
    ):
        try:
            v = getattr(profile, fld, None)
        except Exception:
            v = None
        if v not in (None, "", []):
            snap[fld] = v
    return snap


def _build_contents(
    chat_history: Optional[list[dict]],
    user_text: str,
) -> list[dict]:
    """Translate the orchestrator-style chat_history ({role, content})
    plus the current user_text into Gemini's `contents` payload.

    Gemini wants alternating user/model turns with `parts[].text`.
    `assistant` → `model`; everything else → `user`.
    """
    out: list[dict] = []
    for msg in chat_history or []:
        role = (msg.get("role") or "user").lower()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        gem_role = "model" if role in ("assistant", "model", "bot") else "user"
        out.append({"role": gem_role, "parts": [{"text": content}]})
    out.append({"role": "user", "parts": [{"text": user_text}]})
    return out


# WHOLE-WORD tokens (matched against the tokenised message, NOT as
# substrings — "no" must not match "k(no)ws", "ya" must not match "Ma(ya)").
_RECALL_DENY_TOKENS = {
    "no", "nope", "nah", "naah", "nahi", "wrong", "never", "neither",
    "nopes", "nahin",
}
_RECALL_AFFIRM_TOKENS = {
    "yes", "yeah", "yep", "yup", "ya", "yaa", "yaah", "yess", "haan",
    "han", "haa", "correct", "right", "sahi", "bilkul", "sure", "indeed",
    "absolutely", "exactly", "true", "yup",
}
# Multi-word phrases — safe to match as substrings.
_RECALL_DENY_PHRASES = (
    "not me", "isn't me", "isnt me", "not the same", "start fresh",
    "start over", "different person", "new user", "someone else",
    "not rohit", "first time", "never been", "fresh start", "not him",
    "not her", "i'm new", "im new", "not that person", "don't know",
    "dont know", "different one",
)
_RECALL_AFFIRM_PHRASES = (
    "that's me", "thats me", "that is me", "it's me", "its me", "i am",
    "pick up", "go ahead", "that's right", "thats right", "that's correct",
    "thats correct", "yes please", "continue where", "same person",
    "carry on", "of course",
)
_RECALL_TOKEN_RE = __import__("re").compile(r"[a-z']+")


def _affirm_or_deny(text: str):
    """Conservative yes/no for the returning-user confirm gate.

    Returns True (affirm), False (deny), or None (ambiguous → re-ask).
    Deny wins ties: privacy is fail-closed — an ambiguous "no, but…" must
    NEVER merge a stranger's stored profile (ADR-041 / KI-196). Short
    tokens are matched whole-word (tokenised), not as substrings, so
    "who knows" / "i don't know" / "now" are NOT read as "no".
    """
    t = (text or "").strip().lower()
    if not t:
        return None
    toks = set(_RECALL_TOKEN_RE.findall(t))
    deny = bool(toks & _RECALL_DENY_TOKENS) or any(
        p in t for p in _RECALL_DENY_PHRASES
    )
    if deny:
        return False
    affirm = bool(toks & _RECALL_AFFIRM_TOKENS) or any(
        p in t for p in _RECALL_AFFIRM_PHRASES
    )
    if affirm:
        return True
    return None


def _system_instruction(
    profile, is_returning_user: bool = False, shortlist_block: str = "",
    pending_recall: "Optional[dict]" = None, recall_applied: bool = False,
) -> dict:
    """Bake the profile snapshot into the system prompt so each turn the
    LLM knows what's already captured. Returned in Gemini's expected
    `systemInstruction` shape.

    KI-255 (2026-05-15) — added `is_returning_user` so the LLM can
    distinguish "profile loaded from prior conversation" (RULE 4 Welcome
    Back fires) from "profile captured during THIS turn / earlier in
    this conversation" (no Welcome Back). Smoke-3-personas showed RULE 4
    firing on every first session because the snapshot label said only
    "already captured this session" which Gemini reads as "pre-populated."
    """
    snapshot = _profile_to_snapshot(profile)
    extra = ""
    if snapshot:
        if is_returning_user:
            extra = (
                "\n\nSESSION TYPE: RETURNING USER. Profile below was LOADED FROM A "
                "PRIOR CONVERSATION (the user is coming back). RULE 4 applies — "
                "your first reply must greet by name, summarise, and ask if anything "
                "has changed. After the user confirms or provides new data, proceed."
                "\n\nKNOWN PROFILE (pre-populated from prior session; do NOT re-ask):\n"
                + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            )
        else:
            extra = (
                "\n\nSESSION TYPE: FRESH SESSION. Profile below was CAPTURED IN THIS "
                "CONVERSATION (current turn or earlier turns of this same chat). "
                "RULE 4 does NOT apply — do NOT greet with 'Welcome back', the user "
                "did not come from a prior session. Just continue the conversation "
                "naturally and ask for the next missing slot, or recommend if 7 slots "
                "are filled."
                "\n\nPROFILE CAPTURED IN THIS CONVERSATION (do NOT re-ask, do NOT "
                "say 'Welcome back'):\n"
                + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            )
    recall_block = ""
    if pending_recall:
        _nm = (pending_recall.get("name") or "there").strip()
        _sm = pending_recall.get("summary") or {}
        _bits = []
        for _k in ("age", "location_tier", "dependents", "primary_goal",
                   "health_conditions"):
            _v = _sm.get(_k)
            if _v not in (None, "", []):
                _bits.append(f"{_k.replace('_', ' ')}: {_v}")
        _summ = "; ".join(_bits) if _bits else "a saved profile"
        recall_block = (
            "\n\n═══════════════════════════════════\n"
            "RETURNING-USER CHECK — HIGHEST PRIORITY THIS TURN "
            "(overrides RULE 1 / fact-find for this one turn)\n"
            "═══════════════════════════════════\n"
            f"A stored profile already exists under the name the user just "
            f"gave (\"{_nm}\"). Known hints — {_summ}.\n"
            "Your ENTIRE reply this turn MUST be ONLY the confirmation "
            "question below. Do NOT call any tool, do NOT save_profile_field, "
            "do NOT run the 7-question fact-find, do NOT recommend:\n"
            f"  \"Welcome back — are you the same {_nm} who spoke with us "
            f"before ({_summ})? If yes, I'll pick up right where we left "
            f"off. If not, no problem — just say so and we'll start fresh.\"\n"
            "Then wait for their yes/no on the NEXT turn. The system "
            "applies or discards the saved profile from their answer — you "
            "never merge anything yourself."
        )
    restored_block = ""
    if recall_applied:
        _rs = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        restored_block = (
            "\n\n═══════════════════════════════════\n"
            "RETURNING USER CONFIRMED — PROFILE RESTORED "
            "(HIGHEST PRIORITY THIS TURN)\n"
            "═══════════════════════════════════\n"
            "The user just confirmed they are the SAME returning person. "
            "Their saved profile is RESTORED and FINAL for every slot "
            "present here:\n" + _rs + "\n"
            "Do NOT re-ask, re-confirm, re-verify or 'just double-check' "
            "ANY slot present above — name, age, dependents, city/location, "
            "income band, primary goal, health / pre-existing conditions, "
            "sum insured, existing cover, budget. Re-asking a RESTORED slot "
            "is a hard error: the entire point of recall is that the user "
            "does NOT repeat themselves.\n"
            "Your reply this turn: (1) ONE warm 'welcome back' line, then "
            "(2) resume exactly where a returning user continues — if the "
            "RULE 2.5 pricing inputs (sum insured / premium budget / co-pay "
            "/ smoker / family medical history) are NOT yet captured, ask "
            "ONLY those via the single RULE 2.5 prompt; otherwise go "
            "straight to retrieve_policies + recommendations. Ask ONLY for "
            "a slot that is genuinely ABSENT above — never one present."
        )
    text = (
        SYSTEM_PROMPT + extra + recall_block + restored_block
        + (shortlist_block or "")
    )
    return {"parts": [{"text": text}]}


def _detect_language(user_text: str) -> str:
    """Mirror orchestrator.detect_language at a coarse level so the
    TurnResult.language field stays useful for logging. Devanagari /
    Hinglish → 'indic', else 'en'."""
    if not user_text:
        return "en"
    for ch in user_text:
        # Devanagari range
        if "ऀ" <= ch <= "ॿ":
            return "indic"
    return "en"


# Reply for a turn where the LLM returns no text and no tool calls — a
# transparent retry ask. The single LLM is the only fact-find driver; we
# never fabricate a slot-question.
_HONEST_EMPTY_REPLY = (
    "I'm having trouble generating a response right now — could you "
    "rephrase that, or try again in a moment?"
)


# Bug #108 + #110 (2026-05-16) — explicit-skip detector for the post-recap
# pricing & family-history bundle. When the user clearly declines the
# pricing inputs, single_brain stamps session.pricing_bundle_skipped so
# brain_tools.retrieve_policies' one-shot re-ask gate is BYPASSED (the user
# asked us not to keep asking — SOFT capture means "skip" is honoured).
# Phrase-level only (substring on a lowercased message) so it stays cheap +
# deterministic; a partial answer ("10 lakh cover, skip the rest") still
# counts as skip-the-rest, which is the desired behaviour.
_PRICING_SKIP_PHRASES: tuple[str, ...] = (
    "just show me", "just show options", "just recommend", "just give me",
    "you decide", "you choose", "your call", "whatever you think",
    "skip", "skip the rest", "skip those", "skip that", "no preference",
    "don't have a preference", "dont have a preference", "doesn't matter",
    "doesnt matter", "not sure", "no idea", "show me options",
    "show me the options", "show options", "proceed", "go ahead",
    "let's see options", "lets see options", "recommend now",
)


def _user_skipped_pricing_inputs(user_text: str) -> bool:
    """True when the user's message explicitly declines the pricing /
    family-history bundle (so the deterministic re-ask gate is bypassed)."""
    t = (user_text or "").strip().lower()
    if not t:
        return False
    return any(p in t for p in _PRICING_SKIP_PHRASES)


def _classify_intent(user_text: str, tool_calls_made: list[str]) -> str:
    """Best-effort intent label for logging only. Single-brain doesn't
    route on intent — but the legacy `TurnResult.intent` field is logged
    by main.py and emitted to the frontend."""
    if "retrieve_policies" in tool_calls_made and "mark_recommendation" in tool_calls_made:
        return "recommendation"
    if "retrieve_policies" in tool_calls_made:
        return "qa"
    if "save_profile_field" in tool_calls_made:
        return "fact_find"
    return "qa"


# ---------- Gemini round-trip ----------------------------------------------


async def _gemini_call(
    api_key: str,
    model: str,
    system_instruction: dict,
    contents: list[dict],
    tools: list[dict],
    timeout_sec: float,
) -> dict:
    """Single non-streaming Gemini generateContent call. Returns the raw
    JSON payload. Raises SingleBrainError on any 4xx/5xx/transport error.

    Internal retry: on transient failures (HTTP 429/5xx, httpx
    TimeoutException, httpx.HTTPError) we retry ONCE after a short
    backoff before raising. This soaks up the brief Gemini "high demand"
    503 bursts observed live (2026-05-15) so we don't fall through to
    the legacy orchestrator mid-session for what is usually a sub-second
    blip on the provider side.
    """
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    body: dict = {
        "systemInstruction": system_instruction,
        "contents": contents,
        "tools": [{"functionDeclarations": tools}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {
            "temperature": 0.4,
            # Sized so a turn emitting prose plus a tool-call trailer does
            # not truncate mid-emission (p95 ≈ prose 600 + tool JSON 800 +
            # margin).
            "maxOutputTokens": 2048,
            # gemini-2.5-flash is a thinking model; thinkingBudget=0
            # disables the internal thinking phase so it emits the tool
            # call / text directly (a non-zero budget can consume the
            # output allowance and return an empty completion).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    headers = {"Content-Type": "application/json"}
    client_timeout = httpx.Timeout(
        connect=2.0,
        read=max(2.0, timeout_sec - 2.0),
        write=2.0,
        pool=2.0,
    )

    last_err: Optional[str] = None
    last_status: Optional[int] = None
    # 2 attempts total: initial + 1 retry on transient failure.
    for attempt in range(2):
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except httpx.TimeoutException as e:
                last_err = (
                    f"Gemini timeout after {timeout_sec:.1f}s (model={model})"
                )
                last_status = None
                if attempt == 0:
                    _log.warning(
                        "single_brain transient timeout (attempt=1); "
                        "retrying once after %.1fs backoff",
                        _TRANSIENT_RETRY_BACKOFF_SEC,
                    )
                    await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                    continue
                raise SingleBrainError(last_err) from e
            except httpx.HTTPError as e:
                last_err = (
                    f"Gemini transport error "
                    f"({type(e).__name__}): {str(e)[:200]}"
                )
                last_status = None
                if attempt == 0:
                    _log.warning(
                        "single_brain transient transport error "
                        "(attempt=1, %s); retrying once after %.1fs backoff",
                        type(e).__name__, _TRANSIENT_RETRY_BACKOFF_SEC,
                    )
                    await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                    continue
                raise SingleBrainError(last_err) from e

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.text[:500]
            except Exception:
                pass
            last_status = resp.status_code
            last_err = f"Gemini HTTP {resp.status_code}: {detail}"
            # Transient → retry once. Permanent (4xx like 400/401/403/404) →
            # raise immediately; retrying won't help.
            if (
                attempt == 0
                and resp.status_code in _TRANSIENT_HTTP_CODES
            ):
                _log.warning(
                    "single_brain transient HTTP %d (attempt=1); "
                    "retrying once after %.1fs backoff",
                    resp.status_code, _TRANSIENT_RETRY_BACKOFF_SEC,
                )
                await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                continue
            raise SingleBrainError(last_err)

        try:
            _payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SingleBrainError(f"Gemini malformed JSON: {e}") from e

        # Z2 fix — Issue 1 truncation detector. If Gemini hit our
        # maxOutputTokens budget the candidate's finishReason will be
        # "MAX_TOKENS" and the tool-call trailer (if any) is likely
        # truncated → caller will degrade to the defensive "I lost my
        # train of thought" reply. Log a WARNING (not raise) so the turn
        # still flows, but ops can detect a future budget regression by
        # alerting on this log line. Swallow any shape errors — this is
        # purely observational.
        try:
            _cands = _payload.get("candidates") or []
            if _cands:
                _fr = (_cands[0].get("finishReason") or "").upper()
                if _fr == "MAX_TOKENS":
                    _log.warning(
                        "single_brain Gemini finishReason=MAX_TOKENS "
                        "(model=%s, budget=%d) — prose+tool-call trailer "
                        "may be truncated; raise maxOutputTokens if this "
                        "recurs",
                        model, body["generationConfig"]["maxOutputTokens"],
                    )
        except Exception:  # noqa: BLE001
            pass

        return _payload

    # Defensive — loop fell through without returning or raising. Should
    # be unreachable, but raise so we never silently return None.
    raise SingleBrainError(
        last_err
        or f"Gemini exhausted retries (last_status={last_status})"
    )


# ---------- boot warmup -----------------------------------------------------


async def warmup() -> Optional[float]:
    """Pre-warm the Gemini connection on FastAPI startup.

    The first real /api/chat turn carries 4-5s of cold-start latency:
    HTTPS connection establishment, TLS handshake, Gemini auth, and the
    first response cache init. Firing a tiny dummy request at boot pushes
    that cost off the user's critical path.

    Conditional on USE_SINGLE_BRAIN: if the flag is off, the cold start
    will never matter because single_brain.handle_turn won't run; skip.

    Returns the wall-clock latency in seconds on success, None on skip or
    failure. Never raises — the caller (boot hook) treats any failure as
    a non-fatal warning.
    """
    flag = os.environ.get("USE_SINGLE_BRAIN", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        _log.info("single_brain.warmup skipped — USE_SINGLE_BRAIN is off")
        return None

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        _log.warning("single_brain.warmup skipped — GOOGLE_API_KEY not set")
        return None

    model = _resolve_model()
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": "warmup ping"}]},
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 10},
    }
    headers = {"Content-Type": "application/json"}
    client_timeout = httpx.Timeout(connect=2.0, read=8.0, write=2.0, pool=2.0)

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
        elapsed = time.perf_counter() - t0
        if resp.status_code >= 400:
            _log.warning(
                "single_brain.warmup non-2xx (HTTP %d, %.2fs) — boot continues",
                resp.status_code, elapsed,
            )
            return elapsed
        # Discard payload; we only care about latency + that the round-trip
        # succeeded so the next real call hits a warm socket + auth cache.
        _ = resp.text
        _log.info(
            "single_brain.warmup OK (model=%s, latency=%.2fs)",
            model, elapsed,
        )
        return elapsed
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        _log.warning(
            "single_brain.warmup failed after %.2fs (%s: %s) — boot continues",
            elapsed, type(e).__name__, str(e)[:200],
        )
        return None


def _extract_parts(payload: dict) -> list[dict]:
    """Pull the `parts` list out of the first candidate. Empty list on
    any missing-key path so the caller decides what to do."""
    try:
        candidates = payload.get("candidates") or []
        if not candidates:
            return []
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        if isinstance(parts, list):
            return parts
        return []
    except Exception:  # noqa: BLE001
        return []


def _parts_text(parts: list[dict]) -> str:
    """Concatenate every text part. Empty string when none present."""
    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and "text" in p
    )


# Bug C defensive detector. Brands/products that MUST come from a
# retrieve_policies result. If the bot emits any of these in its reply
# while session.last_retrieved_chunks is empty, log a WARNING so future
# smoke logs can flag hallucinations. Detection-only — does NOT block.
_BRAND_HALLUCINATION_TOKENS = (
    "star health", "hdfc ergo", "niva bupa", "max bupa", "care health",
    "aditya birla", "icici lombard", "bajaj allianz", "manipal cigna",
    "manipalcigna", "acko", "go digit", "godigit", "reliance general",
    "sbi general", "tata aig", "iffco tokio", "cholamandalam",
    "national insurance", "new india assurance", "oriental insurance",
    "united india", "family health optima", "optima secure",
    "reassure", "health companion", "easy health", "activ health",
    "health advantedge", "complete health",
)


def _scan_for_brand_hallucinations(reply_text: str, session) -> None:
    """If the bot mentions an insurer/product brand but session has no
    retrieved chunks, log a WARNING. Detection-only (Bug C secondary
    defense — the system-prompt rule is primary). Swallow any
    exception — bookkeeping must never break a chat turn.
    """
    try:
        if not reply_text:
            return
        last_chunks = getattr(session, "last_retrieved_chunks", None) or []
        if last_chunks:
            return  # retrieve_policies has run; brand mentions are sourced
        haystack = reply_text.lower()
        hits = [tok for tok in _BRAND_HALLUCINATION_TOKENS if tok in haystack]
        if hits:
            _log.warning(
                "single_brain possible policy hallucination — "
                "reply mentions brand(s)=%r but session.last_retrieved_chunks "
                "is empty. session=%s reply_snippet=%r",
                hits,
                getattr(session, "session_id", "?"),
                reply_text[:200],
            )
    except Exception:  # noqa: BLE001 — observational only
        pass


def _verify_prose_grounding(
    reply_text: str, retrieved_chunks_all: list[dict]
) -> tuple[bool, list[str]]:
    """No-invented-numbers guard for REPLY PROSE. Cited cards are grounded
    by construction (hydrated from retrieved chunks); the LLM's prose is
    NOT independently checked since the Path-B rewrite deleted the
    faithfulness validator (faithfulness_passed was hard-coded True). An
    IRDAI UIN is an exact regulator string that can only come from real
    retrieved data — so a UIN written in prose that appears in NO retrieved
    chunk is a fabrication / wrong attribution. Detect + flag only (never
    fabricate, never destructively rewrite). Returns (passed, reasons)."""
    try:
        import re

        if not reply_text:
            return True, []
        uin_re = re.compile(r"\b[A-Z]{3,}[A-Z0-9]{2,}V\d{5,7}\b")
        emitted = set(uin_re.findall(reply_text.upper()))
        if not emitted:
            return True, []
        grounded: set[str] = set()
        for c in retrieved_chunks_all or []:
            for v in (
                c.get("uin_code"), c.get("policy_id"), c.get("policy_name"),
                c.get("chunk_text"), c.get("source_url"),
            ):
                if v:
                    grounded.update(uin_re.findall(str(v).upper()))
        ungrounded = sorted(u for u in emitted if u not in grounded)
        if ungrounded:
            return False, [
                f"reply prose cites UIN(s) absent from every retrieved "
                f"chunk: {ungrounded}"
            ]
        return True, []
    except Exception:  # noqa: BLE001 — guard must never break a turn
        return True, []


def _norm_policy_name(s: str) -> str:
    """Lowercase + collapse punctuation/whitespace for fuzzy prose↔chunk
    name matching. 'my:health Suraksha' / 'my health suraksha' / 'My-Health
    Suraksha' all normalise to 'my health suraksha'."""
    s = (s or "").lower()
    out = []
    prev_space = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        else:
            if not prev_space:
                out.append(" ")
            prev_space = True
    return "".join(out).strip()


# Bug #71 (2026-05-16) — minimum-fit gate for the RECOMMENDED set.
#
# ROOT CAUSE: `_build_recommendation_citations` ranked the cited set by
# gate order but applied NO minimum fit/grade floor. The retrieval pipeline
# (retrieval_filters.rank_by_profile_fit) only RE-ORDERS — it never drops a
# weak-but-best-available plan. So when the LLM cited a B/75 plan AND a
# C/64 plan, BOTH were presented as "recommendations" (the live report:
# HDFC ERGO my:Optima Secure B/75 + Star Family Health Optima C/64). A
# C-graded 64/100 plan for the user's OWN profile is NOT a recommendation.
#
# FIX: a policy only qualifies as a genuine recommendation when its
# scorecard fit clears a sensible floor — overall_score >= 70 (the
# A/B↔C boundary; retrieval_filters._GRADE_POINTS pins B == 70.0), OR,
# when no numeric overall was enriched, a letter grade of A or B. C / D /
# F (or overall < 70) is weak-fit for THIS profile and is dropped from the
# recommended set. We rank the survivors strictly best-first by
# overall_score (gate_rank as the stable tiebreak). If FEWER than the
# intended count clear the bar we cite fewer (or none) — never pad the
# shortlist with a weak plan. We do NOT loosen the scorecard or fabricate;
# we only stop presenting weak-fit plans AS recommendations.
_MIN_RECOMMENDATION_OVERALL: float = 70.0
# Hard ceiling on cited recommendations. The CitedPolicyCards layout
# collapses (names wrap to one character per line) past 3 cards, so the
# recommended set is capped at 3 regardless of how many clear the fitness
# floor — best-first, so the 3 strongest are the ones kept.
_MAX_RECOMMENDATIONS: int = 3
_STRONG_RECOMMENDATION_GRADES: frozenset[str] = frozenset({"A", "B"})


_KNOWN_WEAK_GRADES: frozenset[str] = frozenset({"C", "D", "E", "F"})


def _recommendation_fit(chunk: dict) -> tuple[bool, Optional[float], str]:
    """Return (is_strong_enough, overall_score_or_None, grade_letter).

    A chunk is DROPPED from the recommended set ONLY when we have POSITIVE
    evidence it is a weak fit for THIS profile:
      • its enriched `_overall_score` is present AND < _MIN_RECOMMENDATION_
        OVERALL (the A/B↔C boundary; retrieval_filters._GRADE_POINTS pins
        B == 70.0), OR
      • no numeric overall, but its `_grade` letter is a KNOWN-weak grade
        (C / D / E / F) — the coarse degrade path when the scorecard could
        not produce a numeric. This is the live Bug #71 case: a C/64 plan
        cited as a recommendation.

    FAIL OPEN on MISSING evidence. brain_tools._scorecard_signal is
    explicitly best-effort ("scorecard optional; ranking degrades
    gracefully" — it returns {} on any failure), and the retrieval
    pipeline (retrieval_filters.filter_pipeline) has ALREADY applied
    eligibility + profile-fit before these chunks arrive. So a chunk with
    NO grade and NO overall is treated as strong-enough (kept): silently
    dropping every recommendation whenever the scorecard module is down
    would be a far worse regression than Bug #71. We only gate on plans we
    can affirmatively SEE are weak. This never fabricates; it only gates.
    """
    raw = chunk.get("_overall_score")
    overall: Optional[float]
    try:
        overall = float(raw) if raw is not None and str(raw).strip() != "" else None
    except (TypeError, ValueError):
        overall = None
    grade = str(chunk.get("_grade") or "").strip().upper()[:1]
    if overall is not None:
        # Numeric fitness is authoritative when present.
        return (overall >= _MIN_RECOMMENDATION_OVERALL, overall, grade)
    if grade in _STRONG_RECOMMENDATION_GRADES:
        return (True, None, grade)
    if grade in _KNOWN_WEAK_GRADES:
        return (False, None, grade)
    # No fitness evidence at all → fail OPEN (keep). The pipeline already
    # vetted eligibility/fit; do not nuke the whole shortlist when the
    # optional scorecard enrichment was unavailable.
    return (True, None, grade)


def _build_recommendation_citations(
    reply_text: str,
    retrieved_chunks_all: list[dict],
    marked_policy_ids: list[str],
) -> tuple[list[dict], bool]:
    """Single source of truth for the structured "CITED POLICIES" cards.

    The cited-card set IS exactly the policies the assistant recommended,
    gated by the same fitness logic as the prose:

      1. If the LLM called `mark_recommendation(policy_ids=[...])`, those
         ids are the selection.
      2. Otherwise, parse the reply prose: every retrieved policy whose
         name appears in `reply_text` is selected.

    The cited-card list and the advisory prose are gated by the SAME
    fitness logic, with:

      • CANONICAL DEDUP: `retrieved_chunks_all` is the union of every
        retrieve_policies result this turn. Each result is already gated +
        deduped by retrieval_filters, but across multiple retrieve calls
        the same product can reappear under a doctype-sibling / marketing-
        variant id. We collapse by the shared canonical identity
        (policy_identity.canonical_key) — the SAME rule the marketplace and
        retrieval_filters.dedup_by_policy use — so a product is cited once
        (audit P2/P4/P7).
      • FIT FLOOR + BEST-FIRST ORDER (Bug #71, 2026-05-16): a cited plan
        must clear the recommendation fitness floor (`_recommendation_fit`:
        scorecard overall >= 70, or an A/B letter grade when no numeric
        overall was enriched). Weak-fit plans (C/D/F or overall < 70) are
        DROPPED from the recommended set even if the LLM named them — a
        C-graded plan for the user's OWN profile is not a recommendation.
        Survivors are ordered STRICTLY best-first by scorecard overall
        (gate fit-rank as the stable tiebreak), NOT the LLM's free
        mark_recommendation / prose order, so #1 cited = strongest fit for
        THIS profile. If fewer than the intended count clear the bar we
        cite fewer (or none) — we never pad with a weak plan. This also
        fixes the older audit grade/rank inversion (P1 C/65-above-B/75,
        P2 A/77-ranked-last).

    Each recommended policy is hydrated from its BEST (highest-score)
    retrieved chunk so source_url / policy_name / insurer_slug are real
    corpus values, never invented; `_grade` / `_overall_score` are
    preserved on the card so the fitness signal stays visible downstream.

    Returns (citations, is_recommendation):
      - is_recommendation True  → citations is the prose-aligned, fit-gated
        rec set (may be EMPTY when the LLM recommended but nothing cleared
        the fitness floor — that is CORRECT; the caller must NOT fall back
        to the recall dump and resurrect weak plans).
      - is_recommendation False → no recommendation detected (pure QA /
        chit-chat); caller uses the legacy per-chunk recall list so QA
        answers still get their supporting source chips.
    """
    # KI-280 — collapse the turn's gated chunk stream by CANONICAL identity
    # (UIN-primary, product_key fallback — the shared marketplace/
    # retrieval_filters rule). For each canonical product keep:
    #   • the best (highest-score) chunk for hydration, and
    #   • `gate_rank` = the index of its FIRST appearance in the gated
    #     stream. `retrieved_chunks_all` preserves filter_pipeline's
    #     profile-fit order per retrieve call, so first-appearance order IS
    #     the gate's fit ranking. We order the final cards by this, not by
    #     the LLM's mark_recommendation / prose order.
    best_by_canon: dict[str, dict] = {}
    gate_rank: dict[str, int] = {}
    pid_to_canon: dict[str, str] = {}
    for idx, c in enumerate(retrieved_chunks_all):
        pid = (c.get("policy_id") or "").strip()
        if not pid:
            continue
        canon = canonical_key(c)
        pid_to_canon.setdefault(pid, canon)
        if canon not in gate_rank:
            gate_rank[canon] = idx
        cur = best_by_canon.get(canon)
        if cur is None or float(c.get("score", 0.0) or 0.0) > float(
            cur.get("score", 0.0) or 0.0
        ):
            best_by_canon[canon] = c

    def _cite_canon(canon: str) -> Optional[dict]:
        c = best_by_canon.get(canon)
        if c is None:
            return None
        # Bug #71 — preserve the scorecard fitness signal on the card so the
        # frontend / recommendation-transparency layer can see WHY a plan was
        # (or wasn't) recommended. Previously stripped, which is why a C/64
        # could be presented with no visible grade.
        _strong, _overall, _grade = _recommendation_fit(c)
        _pid = (c.get("policy_id") or "").strip()
        # Link-integrity audit A.3 — the marketplace `policies_all` backfills
        # an empty/non-credible origin source_pdf_url with the local corpus
        # PDF (`/api/policy-pdf/{policy_id}`) we definitively have for every
        # indexed policy, but the citation path historically did not, so 8
        # real policy cards rendered with an empty `source_url` (no PDF chip:
        # `page.tsx` guards on `c.source_url &&`). Mirror the marketplace
        # fallback EXACTLY (main._corpus_pdf_index + main._is_credible_pdf_url,
        # `_cand if credible else (_local or _cand)`) so a cited card never
        # has an empty source_url when a local/marketplace PDF exists. Lazy
        # import: main.py imports single_brain (circular at module scope).
        _src = c.get("source_url", "") or ""
        try:
            from backend.main import (
                _corpus_pdf_index as _cpi,
                _is_credible_pdf_url as _credible,
            )

            _pidx = _cpi()
            _local = (
                f"/api/policy-pdf/{_pid}"
                if (_pid and _pidx.get(_pid))
                else ""
            )
            _src = _src if _credible(_src) else (_local or _src)
        except Exception:  # noqa: BLE001 — fallback must never break citing
            pass
        return {
            "chunk_id": c.get("chunk_id", ""),
            "policy_id": _pid,
            "policy_name": c.get("policy_name", ""),
            "insurer_slug": c.get("insurer_slug", ""),
            "doc_type": c.get("doc_type", ""),
            "source_url": _src,
            "score": c.get("score", 0.0),
            "_grade": _grade or None,
            "_overall_score": _overall,
        }

    def _order_by_gate(canons: list[str]) -> list[dict]:
        """De-dup the selected canonicals, DROP weak-fit plans (Bug #71),
        and emit the survivors STRICTLY best-first.

        Order key: overall_score DESC (strongest fit for THIS profile is
        #1), then the gate's profile-fit rank as a stable tiebreak (so two
        equal-overall plans keep the pipeline's order, and a plan with no
        numeric overall — strong only via an A/B letter grade — sorts after
        numerically-scored peers but still ahead of dropped weak plans). A
        plan that fails the fit floor is removed entirely: if that empties
        the set we return [] (the caller correctly treats an empty rec set
        as 'no strong matches' — it does NOT resurrect the recall dump)."""
        seen: set[str] = set()
        uniq: list[str] = []
        for k in canons:
            if k and k not in seen:
                seen.add(k)
                uniq.append(k)

        scored: list[tuple[float, int, str]] = []
        dropped: list[str] = []
        for k in uniq:
            c = best_by_canon.get(k)
            if c is None:
                continue
            if not brain_tools._has_extraction(c.get("policy_id") or ""):
                # No extracted corpus → the card renders as N/A /
                # "No extraction available for this policy" / "Data not
                # indexed". Drop it from the recommended set even if the
                # LLM named it; only renderable, data-backed policies are
                # ever cited.
                dropped.append(f"{c.get('policy_name') or k}(no-extraction)")
                continue
            strong, overall, _grade = _recommendation_fit(c)
            if not strong:
                dropped.append(
                    f"{c.get('policy_name') or k}"
                    f"(grade={_grade or '?'},overall={overall})"
                )
                continue
            # Sort weight: numeric overall when present (higher = better);
            # an A/B-only plan (overall is None) gets a neutral floor weight
            # so it ranks below numerically-scored strong peers but above
            # everything dropped. Negated so a plain ascending sort puts the
            # strongest first.
            weight = overall if overall is not None else float(
                _MIN_RECOMMENDATION_OVERALL
            )
            scored.append((-weight, gate_rank.get(k, 1_000_000), k))

        scored.sort(key=lambda t: (t[0], t[1]))
        if dropped:
            _log.info(
                "single_brain rec-fit gate (Bug #71): dropped %d weak-fit "
                "plan(s) below overall %.0f / grade A-B: [%s]",
                len(dropped), _MIN_RECOMMENDATION_OVERALL,
                "; ".join(dropped),
            )
        out: list[dict] = []
        for _w, _r, k in scored:
            cite = _cite_canon(k)
            if cite is not None:
                out.append(cite)
            if len(out) >= _MAX_RECOMMENDATIONS:
                break  # hard ≤3 cap — keep the 3 strongest (best-first)
        return out

    # ---- Path 1: explicit mark_recommendation selection -------------------
    if marked_policy_ids:
        # The LLM's ids are the SELECTION; the GATE decides the order.
        selected = [
            pid_to_canon.get((pid or "").strip())
            for pid in marked_policy_ids
        ]
        out = _order_by_gate([k for k in selected if k])
        # mark_recommendation fired ⇒ this is unambiguously a recommendation
        # turn even if id↔chunk hydration matched nothing.
        return out, True

    # ---- Path 2: prose-name matching (LLM forgot mark_recommendation) -----
    haystack = _norm_policy_name(reply_text)
    if not haystack:
        return [], False

    # A canonical product is SELECTED when its best chunk's policy_name is
    # written into the reply prose (longest names matched implicitly via
    # the >=4 char guard so a bare token can't false-match). KI-280: the
    # selection is by prose presence, but the final ORDER is the gate's
    # fit rank (_order_by_gate), not the prose offset — same principle as
    # Path 1, so a forgotten mark_recommendation still yields fit-ordered,
    # canonically-deduped cards.
    selected2: list[str] = []
    for canon, c in best_by_canon.items():
        norm = _norm_policy_name(c.get("policy_name", ""))
        if len(norm) < 4:  # too short to match safely
            continue
        if haystack.find(norm) != -1:
            selected2.append(canon)

    if not selected2:
        # No retrieved policy was named in the prose. If chunks WERE
        # retrieved this is a QA turn that quoted a policy generically →
        # let the caller keep the legacy recall chips for source grounding.
        return [], False

    return _order_by_gate(selected2), True


# ---------------------------------------------------------------------------
# Recommendation-transparency (deploy-#2 follow-up).
#
# CONTEXT (owner Image#8 diagnosis, confirmed): the recommendation-fit gate
# CORRECTLY drops a previously-shown policy the moment a new HARD constraint
# appears (e.g. "Royal Sundaram Multiplier" was shown, then the user says
# "zero co-pay, individual only" → the gate correctly excludes Multiplier
# because it carries a co-pay). The gate logic is RIGHT and is NOT touched
# here. The BUG is purely conversational: the assistant silently swaps the
# recommendation set with NO explanation, so it feels "random / dropped a
# policy" to the user.
#
# Fix: when this turn's gated/cited recommendation set materially differs
# from the previous turn's recommendation (a previously-cited policy is no
# longer cited) BECAUSE the user just stated a new constraint, prepend ONE
# transparent line naming the dropped policy/policies and tying the removal
# to the constraint the user actually stated. Every fact in that line is
# derived from real state — never hallucinated:
#   • dropped policy NAME  ← the prior-turn recommendation snapshot
#     (`session.last_recommendation_snapshot`, id→name, written by us last
#      turn from the real cited set).
#   • the constraint REASON ← `profile_updates`, i.e. the save_profile_field
#     calls the LLM actually made THIS turn from the user's message. We map
#     only KNOWN constraint fields to a human phrase; an unknown field falls
#     back to a generic "based on the preference you just shared" (still
#     accurate, invents no specifics).

# field → (human constraint phrase, predicate the phrase implies). Used to
# turn the REAL save_profile_field call the LLM made this turn into the
# "why" clause. Only fields here produce a specific reason; anything else
# uses the generic phrasing so we never invent a specific that wasn't said.
_CONSTRAINT_FIELD_PHRASES: dict[str, str] = {
    "copay_pct": "you want zero co-pay",
    "deductible_amount": "you set a deductible preference",
    "desired_sum_insured_inr": "you set a sum-insured target",
    "budget_band": "you gave a budget",
    "parents_to_insure": "you're now insuring parents",
    "parents_age_max": "of the parents' age",
    "health_conditions": "of the health condition you mentioned",
    "smoker": "of the tobacco-use detail you shared",
}


def _constraint_reason_clause(profile_updates: dict) -> str:
    """Derive the 'why' clause from the REAL save_profile_field calls the
    LLM made this turn (never invented). Special-case copay_pct == 0 →
    'you want zero co-pay' (the canonical Image#8 scenario); otherwise use
    the field's mapped phrase, else a generic preference phrase."""
    if not profile_updates:
        return "based on the preference you just shared"
    # Prefer a specific, recognised constraint field.
    for fld, phrase in _CONSTRAINT_FIELD_PHRASES.items():
        if fld not in profile_updates:
            continue
        val = profile_updates.get(fld)
        if fld == "copay_pct":
            try:
                if int(str(val).strip() or "0") == 0:
                    return "you want zero co-pay"
            except (TypeError, ValueError):
                pass
            return "of the co-pay preference you set"
        return phrase
    return "based on the preference you just shared"


def _recommendation_change_note(
    prev_snapshot: dict,
    current_citations: list[dict],
    profile_updates: dict,
) -> str:
    """Return a single transparent sentence to PREPEND to the reply when a
    previously-recommended policy is no longer in the cited set because the
    user just stated a constraint — else "".

    prev_snapshot     : {policy_id: policy_name} from LAST turn's cited set.
    current_citations  : THIS turn's gated rec citations (post-fit gate).
    profile_updates    : save_profile_field calls the LLM made THIS turn.

    Guard rails (no spurious note):
      • no prior recommendation snapshot           → ""
      • no NEW constraint persisted this turn       → "" (a set change with
        no new constraint is a normal refinement, not a silent drop)
      • current cited set empty                     → "" (separate
        no-results path; nothing to "swap to")
      • nothing actually dropped (every prior id    → "" (set unchanged /
        still cited, possibly reordered/added)         only grew)
    """
    if not prev_snapshot or not profile_updates or not current_citations:
        return ""

    # Canonicalise both sides so a doctype-sibling / marketing-variant id
    # isn't mis-counted as "dropped" — same identity rule the citation
    # builder + marketplace dedup use.
    cur_canon: set[str] = set()
    for c in current_citations:
        try:
            cur_canon.add(canonical_key(c))
        except Exception:  # noqa: BLE001 — identity helper must not break turn
            pid = (c.get("policy_id") or "").strip()
            if pid:
                cur_canon.add(pid)
    cur_names_norm = {
        _norm_policy_name(c.get("policy_name", "")) for c in current_citations
    }

    dropped: list[str] = []
    seen_norm: set[str] = set()
    for pid, pname in prev_snapshot.items():
        pid = (pid or "").strip()
        name = (pname or "").strip()
        if not name:
            continue
        # Reconstruct a minimal chunk so canonical_key matches the builder's
        # input shape; fall back to the raw id if identity can't resolve.
        try:
            pcanon = canonical_key({"policy_id": pid, "policy_name": name})
        except Exception:  # noqa: BLE001
            pcanon = pid
        norm = _norm_policy_name(name)
        still_cited = pcanon in cur_canon or (norm and norm in cur_names_norm)
        if still_cited or norm in seen_norm:
            continue
        seen_norm.add(norm)
        dropped.append(name)

    if not dropped:
        return ""

    reason = _constraint_reason_clause(profile_updates)
    if len(dropped) == 1:
        removed = dropped[0]
    elif len(dropped) == 2:
        removed = f"{dropped[0]} and {dropped[1]}"
    else:
        removed = ", ".join(dropped[:-1]) + f", and {dropped[-1]}"
    verb = "it doesn't" if len(dropped) == 1 else "they don't"
    return (
        f"Since {reason}, I've removed {removed} from the shortlist "
        f"({verb} fit that), and these now fit better:"
    )


def _parts_function_calls(parts: list[dict]) -> list[dict]:
    """Pull every functionCall block out of parts. Each entry is
    {"name": "...", "args": {...}}."""
    out: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if isinstance(fc, dict) and fc.get("name"):
            out.append(
                {
                    "name": fc.get("name"),
                    "args": fc.get("args") or {},
                }
            )
    return out


async def _execute_tool(session, name: str, args: dict) -> dict:
    """Dispatch a single function call to the matching brain_tools function.
    Returns the JSON-serialisable response dict that gets fed back to Gemini
    on the next turn."""
    try:
        if name == "save_profile_field":
            return brain_tools.save_profile_field(
                session,
                field=args.get("field", ""),
                value=args.get("value"),
            )
        if name == "retrieve_policies":
            return await brain_tools.retrieve_policies(
                query=args.get("query", ""),
                top_k=int(args.get("top_k") or 8),
                policy_filter_ids=args.get("policy_filter_ids") or None,
                profile=getattr(session, "profile", None),
                intent="recommendation",
                session=session,
                # QUARANTINE-RETRIEVAL FIX (2026-05-16) — forward the live
                # chat session_id explicitly so an uploaded PDF (indexed in
                # the per-session quarantine collection) is retrievable by
                # the brain for THIS session only. Without this the upload
                # was embedded but never surfaced in the conversation.
                session_id=getattr(session, "session_id", None),
            )
        if name == "mark_recommendation":
            return brain_tools.mark_recommendation(
                session,
                policy_ids=args.get("policy_ids") or [],
                is_final=bool(args.get("is_final") or False),
            )
        if name == "get_policy_facts":
            return brain_tools.get_policy_facts(
                session,
                policy_ids=args.get("policy_ids") or None,
            )
        return {"ok": False, "error": f"unknown_tool:{name}"}
    except Exception as e:  # noqa: BLE001 — never crash the loop
        _log.warning(
            "tool=%s args=%r raised %s: %s",
            name, args, type(e).__name__, str(e)[:200],
        )
        return {"ok": False, "error": f"{type(e).__name__}:{str(e)[:200]}"}


# ---------- main entrypoint ------------------------------------------------


async def handle_turn(
    session,
    user_text: str,
    chat_history: Optional[list[dict]] = None,
) -> TurnResult:
    """Single-LLM turn handler — replaces orchestrator.handle_turn behaviour
    when USE_SINGLE_BRAIN is enabled.

    Returns a TurnResult whose shape matches orchestrator.TurnResult.
    Raises SingleBrainError on unrecoverable Gemini failure so the api.py
    caller falls through to the legacy orchestrator.
    """
    t0 = time.time()

    # X7 — monotonic conversation-turn counter; admin Recommendation History
    # renders this as the "Conversation turn" column. Increment BEFORE any
    # tool call so brain_tools.mark_recommendation can stamp the resulting
    # turn_idx onto each shown_policies event written this turn.
    try:
        session.turn_idx = int(getattr(session, "turn_idx", 0) or 0) + 1
    except Exception:  # noqa: BLE001 — never break a chat turn for bookkeeping
        pass

    # GOOGLE_API_KEY gate is asserted below, just before the Gemini call.
    model = _resolve_model()
    language = _detect_language(user_text)

    # KI-255 — detect "returning user" so RULE 4 (Welcome Back greeting)
    # only fires when the profile was actually loaded from a prior session.
    # Signal: session.turn_idx == 1 (first turn of this session_id) AND the
    # profile already has a captured slot (hydrated from prior persistence).
    # turn_idx > 1 ⇒ slots filled by save_profile_field within THIS
    # conversation — not a returning user.
    _current_turn = int(getattr(session, "turn_idx", 1) or 1)

    # ── Returning-user recall (ADR-041 / KI-196), wired into single_brain
    # 2026-05-19. Previously ORPHANED by the orchestrator→single-LLM
    # rewrite: extract_potential_name / try_recall_by_name /
    # apply_pending_recall existed and were unit-tested, but NOTHING on the
    # live path called them — so a same-name revisit ("Hi, I'm Rohit") was
    # never recognised and the "are you the same Rohit?" prompt never fired.
    # Privacy-safe by construction: a name match is only STAGED on
    # session.pending_profile_recall (never auto-merged); only an explicit
    # "yes" merges the stored slots, an explicit "no" discards, anything
    # ambiguous leaves it staged so the LLM re-asks the confirm once.
    _did_recall_this_turn = False
    try:
        from backend.profile_persistence import (
            extract_potential_name,
            try_recall_by_name,
        )
        from backend.session_state import apply_pending_recall

        _pending_recall = getattr(session, "pending_profile_recall", None)
        if _pending_recall:
            _ans = _affirm_or_deny(user_text)
            if _ans is True:
                _did_recall_this_turn = bool(
                    apply_pending_recall(session, confirmed=True)
                )
                _pending_recall = None
            elif _ans is False:
                apply_pending_recall(session, confirmed=False)
                _pending_recall = None
            # ambiguous → leave staged; the confirm block is re-injected
            # below and the LLM re-asks the "are you <name>?" question.
        elif _current_turn == 1:
            _nm = extract_potential_name(user_text or "")
            if _nm:
                # Stages session.pending_profile_recall iff a stored
                # profile for this name exists (no match ⇒ no-op, normal
                # fresh-user flow continues — no false confirm prompt).
                try_recall_by_name(session, _nm)
                _pending_recall = getattr(
                    session, "pending_profile_recall", None
                )
    except Exception as _re:  # noqa: BLE001 — recall must never break a turn
        _log.warning(
            "returning-user recall wiring failed: %s: %s",
            type(_re).__name__, str(_re)[:200],
        )
        _pending_recall = getattr(session, "pending_profile_recall", None)

    _has_prior_profile = any(
        getattr(session.profile, fld, None) not in (None, "", [])
        for fld in (
            "name", "age", "dependents", "location_tier",
            "income_band", "primary_goal", "health_conditions",
        )
    )
    is_returning_user = (_current_turn == 1) and _has_prior_profile

    # GOOGLE_API_KEY gate — asserted just before anything that talks to
    # Gemini.
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise SingleBrainError("GOOGLE_API_KEY not set")

    # ACTIVE SHORTLIST — the policies recommended on a prior turn. RULE 7 /
    # RULE 3.5 / RULE 5 reference session.last_recommendation_ids, but the
    # model was never actually SHOWN it (root cause, 2026-05-18): without
    # this block "compare #1 and #2" / "claim ratio of the HDFC one" could
    # not resolve to policy_ids, so the model refused. Surface id + name +
    # insurer so the model can pass exact policy_ids to get_policy_facts /
    # retrieve_policies.
    _shortlist_block = ""
    try:
        _sl_ids = list(getattr(session, "last_recommendation_ids", []) or [])
        if _sl_ids:
            _snap = dict(
                getattr(session, "last_recommendation_snapshot", {}) or {}
            )
            _s2i = dict(getattr(session, "slug_to_insurer", {}) or {})
            _lines = []
            for _i, _pid in enumerate(_sl_ids, 1):
                _nm = _snap.get(_pid) or _pid
                _ins = _s2i.get(_pid) or ""
                _lines.append(
                    f"  #{_i}  policy_id={_pid}  |  {_nm}"
                    + (f"  ({_ins})" if _ins else "")
                )
            _shortlist_block = (
                "\n\n═══════════════════════════════════\n"
                "ACTIVE SHORTLIST (policies you recommended this session — "
                "resolve \"#1/#2/the X one/the two you showed\" to THESE "
                "policy_ids and pass them to get_policy_facts / "
                "retrieve_policies):\n" + "\n".join(_lines)
            )
    except Exception:  # noqa: BLE001 — bookkeeping must not break a turn
        _shortlist_block = ""

    system_instruction = _system_instruction(
        session.profile,
        is_returning_user=is_returning_user,
        shortlist_block=_shortlist_block,
        pending_recall=_pending_recall,
        recall_applied=_did_recall_this_turn,
    )

    # Bug #108 + #110 — if the user explicitly declines the pricing /
    # family-history bundle on THIS turn, stamp the session so
    # brain_tools.retrieve_policies' one-shot re-ask gate is bypassed (a
    # skip is honoured under SOFT-capture semantics; we never nag). Sticky
    # for the rest of the session — once the user says "just show me
    # options" we don't re-gate the bundle on later recommendation turns.
    try:
        if _user_skipped_pricing_inputs(user_text):
            session.pricing_bundle_skipped = True
    except Exception:  # noqa: BLE001 — never break a turn for this
        pass

    # The running `contents` list — we append model turns + function
    # responses to it across loop iterations so Gemini sees the entire
    # tool-call thread when emitting its final text.
    contents = _build_contents(chat_history, user_text)

    # Track each tool call we serve so we can populate citations + the
    # `intent`/`brain_used` log fields at the end.
    tool_calls_made: list[str] = []
    retrieved_chunks_all: list[dict] = []
    last_marked_policy_ids: list[str] = []
    profile_updates: dict[str, Any] = {}

    # Recommendation-transparency (deploy-#2 follow-up). Capture the PREVIOUS
    # turn's recommended set NOW — before the iteration loop runs
    # mark_recommendation, which overwrites session.last_recommendation_ids
    # and session.last_recommendation_snapshot for THIS turn. The snapshot
    # is {policy_id: policy_name} written by us at the end of the prior
    # recommendation turn, so we can NAME a dropped policy next turn even if
    # the fit gate excludes it from this turn's retrieval entirely.
    prev_rec_snapshot: dict[str, str] = dict(
        getattr(session, "last_recommendation_snapshot", {}) or {}
    )

    # Defensive counter to break runaway loops.
    last_text: str = ""
    last_payload: dict = {}

    for it in range(MAX_ITERATIONS):
        # Issue A instrumentation (KI-Z6-LATENCY, 2026-05-15) — Priya T3
        # timed at 18.7s vs an 8s budget. We need per-iteration breakdown
        # of (Gemini call time) vs (tool exec time) to identify whether
        # cold-start, embedding/Chroma, or sequential LLM calls dominate.
        # Wall-clock timers below feed `_log.info("iter %d: ...")` so HF
        # Space logs surface the breakdown without any extra plumbing.
        _t_iter0 = time.perf_counter()
        try:
            payload = await _gemini_call(
                api_key=api_key,
                model=model,
                system_instruction=system_instruction,
                contents=contents,
                tools=TOOL_SCHEMAS,
                timeout_sec=PER_CALL_TIMEOUT_SEC,
            )
        except SingleBrainError:
            raise
        except Exception as e:  # noqa: BLE001 — defensive
            raise SingleBrainError(
                f"gemini_call unexpected error: {type(e).__name__}: {e}"
            ) from e
        _t_gemini = time.perf_counter() - _t_iter0

        last_payload = payload
        parts = _extract_parts(payload)
        function_calls = _parts_function_calls(parts)
        text = _parts_text(parts).strip()

        # CASE A — no function calls: this is the final text reply.
        # Includes the "Gemini just chats on turn 1" path the spec
        # called out — completely valid, return immediately.
        #
        # BUGFIX (2026-05-18) — same destructive-overwrite class as the
        # CASE-B site below. When the FINAL iteration carries no function
        # calls AND an empty text part (the documented near-zero "LLM
        # returned nothing" tail referenced at the reply_text fallback
        # comment), an unconditional `last_text = text` here wiped prose
        # captured in a PRIOR tool-call iteration → reply_text fell through
        # to _HONEST_EMPTY_REPLY → main.py skipped TTS (bot went SILENT
        # after profile completion / policy presentation). Only adopt this
        # iteration's text when it actually produced prose; an empty final
        # text now falls back to the earlier spoken prose instead of
        # destroying it. A non-empty final text still replaces it (the
        # normal "model's last word wins" path is unchanged).
        if not function_calls:
            if text:
                last_text = text
            _log.info(
                "single_brain iter=%d gemini=%.2fs tools=%.2fs "
                "tool_calls=[] final_text=True",
                it, _t_gemini, 0.0,
            )
            break

        # CASE B — one or more function calls. Append the model turn
        # verbatim so Gemini sees its own previous tool-call request,
        # then execute every call and append a single user turn with
        # the matching functionResponse parts.
        contents.append(
            {
                "role": "model",
                "parts": parts,
            }
        )

        _t_tools0 = time.perf_counter()
        _per_tool_latency: list[str] = []  # logged tail for iter summary
        response_parts: list[dict] = []
        for fc in function_calls:
            name = fc["name"]
            args = fc.get("args") or {}
            tool_calls_made.append(name)
            _t_tool0 = time.perf_counter()
            result = await _execute_tool(session, name, args)
            _t_tool = time.perf_counter() - _t_tool0
            _per_tool_latency.append(f"{name}={_t_tool:.2f}s")

            # Issue A — when retrieve_policies dominates iter latency we
            # need to know whether it's the embedding step or the Chroma
            # ANN query. brain_tools.retrieve_policies already returns
            # chunks + count; surface the elapsed wall-clock here so the
            # log line tags retrieve_policies separately. The deeper
            # embedding vs Chroma breakdown lives inside rag.retrieve and
            # is out of scope for this patch; this gives ops enough signal
            # to decide whether to drill further.
            if name == "retrieve_policies":
                _log.info(
                    "single_brain retrieve_policies elapsed=%.2fs "
                    "chunks=%d query_len=%d filter_ids=%s",
                    _t_tool,
                    len(result.get("chunks") or []),
                    len(str(args.get("query") or "")),
                    bool(args.get("policy_filter_ids")),
                )

            # Bookkeeping for the TurnResult fields.
            if name == "save_profile_field" and result.get("saved"):
                fld = result.get("field")
                if fld:
                    profile_updates[fld] = result.get("value")
            elif name == "retrieve_policies":
                for c in result.get("chunks") or []:
                    retrieved_chunks_all.append(c)
            elif name == "mark_recommendation" and result.get("recorded"):
                last_marked_policy_ids = list(result.get("policy_ids") or [])

            response_parts.append(
                {
                    "functionResponse": {
                        "name": name,
                        "response": {"content": result},
                    }
                }
            )
        _t_tools = time.perf_counter() - _t_tools0

        _log.info(
            "single_brain iter=%d gemini=%.2fs tools=%.2fs "
            "tool_calls=[%s] per_tool=[%s]",
            it, _t_gemini, _t_tools,
            ",".join(fc["name"] for fc in function_calls),
            " ".join(_per_tool_latency),
        )

        contents.append({"role": "user", "parts": response_parts})
        # And loop — Gemini gets another shot to either call more
        # tools or emit a final text reply.
        #
        # BUGFIX (2026-05-18) — only update last_text when THIS iteration
        # actually produced prose. An unconditional `last_text = text` here
        # erased prose captured in a PRIOR iteration whenever a later
        # iteration returned only function calls (text == ""): e.g. iter 1
        # "Great, your profile is complete! …" + save_profile_field, then
        # iter 2 retrieve_policies + mark_recommendation with NO text →
        # last_text became "" → reply_text fell through to
        # _HONEST_EMPTY_REPLY → main.py skipped TTS (bot went SILENT right
        # after profile completion / policy presentation). The original
        # intent — keep the latest prose so a MAX_ITERATIONS exit still has
        # a non-empty reply — is preserved: a non-empty text on any
        # iteration still updates last_text; an empty one is now a no-op
        # instead of a destructive overwrite.
        if text:
            last_text = text
    else:
        # Hit MAX_ITERATIONS without break — honest signal, not a
        # fabricated slot-question.
        _log.warning(
            "single_brain hit MAX_ITERATIONS=%d (tool_calls=%s)",
            MAX_ITERATIONS, tool_calls_made,
        )
        last_text = last_text or _HONEST_EMPTY_REPLY

    # Build TurnResult. An empty last_text here is a genuine LLM failure
    # (near-zero with thinkingConfig set) — surface it honestly.
    reply_text = last_text or _HONEST_EMPTY_REPLY

    # Bug C secondary defense — log a WARNING if the reply name-drops an
    # insurer/product brand even though no retrieve_policies result was
    # cached on the session. The system-prompt ABSOLUTE RULE is the
    # primary defense; this only exists so a future regression shows up
    # in smoke logs instead of going silent.
    _scan_for_brand_hallucinations(reply_text, session)

    # retrieved_chunk_ids — full recall set, deduped by chunk_id. Kept for
    # logging / faithfulness / KI-254 routing parity regardless of which
    # citation path we take below.
    seen_ids: set[str] = set()
    retrieved_chunk_ids: list[str] = []
    recall_citations: list[dict] = []
    for c in retrieved_chunks_all:
        cid = c.get("chunk_id") or ""
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)
        retrieved_chunk_ids.append(cid)
        recall_citations.append(
            {
                "chunk_id": cid,
                "policy_id": c.get("policy_id", ""),
                "policy_name": c.get("policy_name", ""),
                "insurer_slug": c.get("insurer_slug", ""),
                "doc_type": c.get("doc_type", ""),
                "source_url": c.get("source_url", ""),
                "score": c.get("score", 0.0),
            }
        )

    # KI-278 — SINGLE SOURCE OF TRUTH for the "CITED POLICIES" cards.
    # Previously `citations` WAS `recall_citations` (the raw vector-score
    # recall dump), so the cards listed policies the LLM never named and
    # dropped ones it did. Now the citation set IS exactly the policies the
    # assistant recommended, in prose order: explicit mark_recommendation
    # ids when present, else the policy names actually written in the reply.
    rec_citations, is_recommendation = _build_recommendation_citations(
        reply_text=reply_text,
        retrieved_chunks_all=retrieved_chunks_all,
        marked_policy_ids=last_marked_policy_ids,
    )
    # Bug #107 (2026-05-16) — FACT-FIND / CLARIFYING TURNS CARRY NO
    # CITATIONS.
    #
    # ROOT CAUSE: on a non-recommendation turn `citations` fell back to
    # `recall_citations` (the raw retrieve_policies recall dump). When the
    # LLM speculatively called retrieve_policies and THEN asked the user a
    # clarifying / fact-find question (no policy named in prose, no
    # mark_recommendation), that dump still flowed to the frontend, which
    # rendered a full ranked "CITED POLICIES" list directly under a reply
    # that says "Before I recommend… I need more info" — the cards
    # contradict the message.
    #
    # "FACT-FIND vs RECOMMENDATION turn" detection (deterministic, no LLM):
    #   • RECOMMENDATION turn  ⇔ `is_recommendation` is True
    #       (_build_recommendation_citations saw an explicit
    #        mark_recommendation OR a retrieved policy NAMED in the reply
    #        prose) AND the required-slot profile gate is satisfied
    #        (brain_tools._profile_complete — the SAME gate
    #        retrieve_policies and main._compute_profile_complete use).
    #   • Anything else (profile gate NOT satisfied, or no policy
    #     recommended) is a FACT-FIND / clarifying / chit-chat turn.
    #
    # Only a RECOMMENDATION turn attaches policy citations. A fact-find or
    # clarifying turn returns an EMPTY list so the UI renders nothing under
    # the question. This also covers the spec's "no recommendation made /
    # profile gate not satisfied" wording exactly.
    try:
        _profile_gate_ok = brain_tools._profile_complete(session.profile)
    except Exception:  # noqa: BLE001 — gate read must never break a turn
        _profile_gate_ok = False
    _is_recommendation_turn = bool(is_recommendation) and _profile_gate_ok

    if _is_recommendation_turn:
        # Recommendation turn — cards mirror the prose 1:1 (same count,
        # same order). An empty rec set here is CORRECT (do not fall back
        # to the recall dump and resurrect un-named policies).
        citations = rec_citations
    else:
        # FACT-FIND / clarifying / chit-chat turn — NO policy citations.
        # Even if retrieve_policies ran speculatively this turn, the user
        # is being asked for more info, not given a recommendation; the
        # ranked-card UI must not contradict the question (Bug #107).
        citations = []

    if len(citations) != len(recall_citations):
        _log.info(
            "single_brain citations: rec=%d recall=%d is_rec=%s "
            "profile_gate_ok=%s rec_turn=%s marked=%d "
            "(KI-278 prose-aligned; Bug #107 fact-find gate)",
            len(citations), len(recall_citations), is_recommendation,
            _profile_gate_ok, _is_recommendation_turn,
            len(last_marked_policy_ids),
        )

    # ---- Recommendation-transparency (deploy-#2 follow-up) ----------------
    # The fit gate (correct, untouched) silently swaps the rec set when a
    # new hard constraint drops a previously-shown policy. Make it
    # transparent: if a policy from the PREVIOUS turn's cited set is no
    # longer cited AND the user persisted a new constraint THIS turn,
    # prepend one line naming the dropped policy/policies and tying the
    # removal to the constraint the user actually stated. Every fact is
    # derived from real state (prior snapshot + this turn's
    # save_profile_field calls) — nothing is invented. Only on
    # recommendation turns; the citation/gate behaviour is unchanged.
    # Bug #107 — use the GATED recommendation-turn signal (is_recommendation
    # AND profile gate satisfied) so a fact-find turn that speculatively
    # retrieved never emits a drop note or overwrites the rec snapshot.
    if _is_recommendation_turn:
        _change_note = _recommendation_change_note(
            prev_snapshot=prev_rec_snapshot,
            current_citations=citations,
            profile_updates=profile_updates,
        )
        if _change_note:
            reply_text = f"{_change_note}\n\n{reply_text}"
            _log.info(
                "single_brain rec-transparency: prepended drop note "
                "(prev=%d cur=%d updates=%s)",
                len(prev_rec_snapshot), len(citations),
                sorted(profile_updates.keys()),
            )

    # Persist THIS turn's cited set as the snapshot the NEXT turn diffs
    # against. {policy_id: policy_name}. Only overwrite on a real
    # recommendation turn so a follow-up QA/chit-chat turn (is_recommendation
    # False, no shortlist) doesn't erase the active shortlist's identity and
    # blind the next constraint-driven swap. brain_tools.mark_recommendation
    # already wrote last_recommendation_ids; this name-bearing snapshot is
    # written here (single_brain owns it; brain_tools is out of scope).
    if _is_recommendation_turn and citations:
        try:
            session.last_recommendation_snapshot = {
                (c.get("policy_id") or "").strip(): c.get("policy_name", "")
                for c in citations
                if (c.get("policy_id") or "").strip()
            }
        except Exception:  # noqa: BLE001 — bookkeeping must not break a turn
            pass

    intent = _classify_intent(user_text, tool_calls_made)
    brain_used = f"single_brain::{model}"
    if tool_calls_made:
        brain_used += f"::tools={'+'.join(sorted(set(tool_calls_made)))}"

    # follow-up policy id: if the LLM marked exactly one policy this turn,
    # surface it so the frontend can highlight the matching card.
    followup_policy_id = (
        last_marked_policy_ids[0]
        if len(last_marked_policy_ids) == 1
        else None
    )

    # Prose-faithfulness guard (no-invented-numbers). Cited cards are safe
    # by construction; this catches a UIN written in PROSE that no
    # retrieved chunk supports. Flag + transparent caveat — never fabricate
    # or silently delete.
    _faith_ok, _faith_reasons = _verify_prose_grounding(
        reply_text, retrieved_chunks_all
    )
    if not _faith_ok:
        _log.warning(
            "single_brain prose-faithfulness FAIL — %s | session=%s "
            "snippet=%r",
            _faith_reasons,
            getattr(session, "session_id", "?"),
            reply_text[:200],
        )
        reply_text += (
            "\n\n⚠️ One or more policy identifiers above could not be "
            "verified against our records — please confirm the UIN with "
            "the insurer before relying on it."
        )

    return TurnResult(
        reply_text=reply_text,
        citations=citations,
        retrieved_chunk_ids=retrieved_chunk_ids,
        brain_used=brain_used,
        intent=intent,
        language=language,
        latency_ms=int((time.time() - t0) * 1000),
        raw_reply=json.dumps(last_payload)[:4000] if last_payload else reply_text,
        faithfulness_passed=_faith_ok,
        faithfulness_reasons=_faith_reasons,
        blocked=False,
        profile_updates=profile_updates,
        followup_policy_id=followup_policy_id,
        returning_user_recalled=_did_recall_this_turn,
    )


__all__ = [
    "SingleBrainError",
    "TurnResult",
    "handle_turn",
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
    "MAX_ITERATIONS",
    "PER_CALL_TIMEOUT_SEC",
]
