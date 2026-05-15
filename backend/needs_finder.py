"""Adaptive fact-find question graph.

Inspired by what a good Independent Financial Advisor does in the first 10
minutes of a consultation: ask a stable core of questions, then deep-dive
conditionally based on signal. The depth is adaptive — stop when we have
enough to recommend.

The graph is explicit (not LLM-improvised) so:
  - A reviewer can see and audit it
  - Behavior is testable
  - Failure modes are tractable
  - It works without an LLM (fallback when the brain is degraded)

Public API:
  - Profile dataclass — accumulated user state
  - next_question(profile) -> str | None   (None = ready to recommend)
  - record_answer(profile, question_id, answer) -> Profile
  - readback_summary(profile) -> str

The orchestrator can choose to drive the fact-find OR let the user
free-form questions — the graph supports both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Profile:
    """User profile accumulated during fact-find."""
    name: Optional[str] = None  # KI-040 — humanise + key for cross-session lookup
    age: Optional[int] = None
    dependents: Optional[str] = None  # "self", "self+spouse", "self+spouse+kids", "self+parents", etc.
    income_band: Optional[str] = None  # "under_5L", "5L-10L", "10L-25L", "25L+"
    existing_cover_inr: Optional[int] = None  # 0 means none
    primary_goal: Optional[str] = None  # "first_buy", "upgrade", "compare_specific", "tax_planning"
    location_tier: Optional[str] = None  # "metro", "tier1", "tier2", "tier3"
    parents_to_insure: Optional[bool] = None
    parents_age_max: Optional[int] = None  # if parents_to_insure
    parents_has_ped: Optional[bool] = None  # if parents_to_insure
    budget_band: Optional[str] = None  # "under_15k", "15k_30k", "30k_60k", "60k+"
    health_conditions: Optional[list[str]] = field(default_factory=list)  # ["diabetes", "hypertension", ...]
    asked: list[str] = field(default_factory=list)  # question IDs already asked
    free_form_session: bool = False  # True = user asks free questions, not driven by us
    # KI-063 (2026-05-15) — per-user policy interaction log so the bot
    # remembers which policies were shown / selected / rejected across
    # sessions. Each entry is a dict with shape:
    #   {policy_slug, insurer, event_at (ISO Z), session_id, reason}
    # Dedup at write-time on (policy_slug, event_type) — re-events just
    # bump event_at + session_id rather than appending duplicates.
    shown_policies: list[dict] = field(default_factory=list)     # KI-063
    selected_policies: list[dict] = field(default_factory=list)  # KI-063
    rejected_policies: list[dict] = field(default_factory=list)  # KI-063


# ----------------------------------------------------------------------------
# Question graph — each node has an id, a prompt, and a condition for being
# asked. Conditions are pure functions of the current Profile.
# ----------------------------------------------------------------------------

@dataclass
class Question:
    id: str
    prompt_en: str
    prompt_hi: str  # optional Hindi rendering (used when language='indic')
    field: str
    is_core: bool = False
    condition: Any = None  # callable(profile) -> bool, default: always ask
    parser: Any = None  # callable(text) -> value, default: text as-is


def _always(p: Profile) -> bool:
    return True


# ----------------------------------------------------------------------------
# KI-149 (2026-05-15) — free-text INR amount parser for budget + income.
# User said "I maximum 30000 I can pay" → bot re-asked budget because no
# parser was attached to the budget Question and the LLM brain failed to
# capture it. Bare digits ("30000"), "30 thousand", "30 grand", "₹30,000",
# "1 lakh", "1.5L" must all map cleanly to a rupee amount.
# ----------------------------------------------------------------------------

def _parse_inr_amount(text: str) -> Optional[int]:
    """Extract an INR amount in rupees from free text.

    Handles:
      - "30000", "30,000", "₹30,000", "Rs 30000", "rs. 30000"
      - "30k", "30 k", "30K"
      - "30 thousand", "30 grand"
      - "1 lakh", "1.5 lakh", "1L", "1.5L", "1 lac"
      - "1 crore", "1cr"
      - strips fluff: "maximum 30000", "I can pay 30000", "around 25000"
      - tolerates per-year qualifiers: "/year", "per year", "p.a."

    Returns the integer rupee amount, or None if no number is recognisable.
    """
    if not text:
        return None
    s = str(text).lower().strip()
    # Strip currency symbols + thousands separators so "₹30,000" parses.
    s = s.replace("₹", " ").replace("rs.", " ").replace("rs", " ")
    s = s.replace(",", "")
    # Crore (highest unit first so longer alternation wins).
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:cr|crore|crores)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 10_000_000)
        except ValueError:
            return None
    # Lakh / lac.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:l(?:akh|ac)?s?)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 100_000)
        except ValueError:
            return None
    # Thousand / grand / k.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:thousand|grand|k)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 1_000)
        except ValueError:
            return None
    # Bare digit run — pick the largest number-like token (handles
    # "maximum 30000", "around 25000", "I can pay 30000").
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if nums:
        try:
            return int(float(max(nums, key=lambda x: float(x))))
        except ValueError:
            return None
    return None


def _parse_budget_band(text: str) -> Optional[str]:
    """Map free-text budget text → one of under_15k / 15k_30k / 30k_60k / 60k+.

    KI-149 (2026-05-15). Falls back to range hints ("15-30k", "30 to 60k")
    before delegating to `_parse_inr_amount` for a single number.
    """
    if not text:
        return None
    s = str(text).lower()
    # Explicit bucket hints first — order matters (more specific wins).
    if re.search(r"60\s*k\s*\+|>\s*60|more\s+than\s+60|above\s+60|over\s+60", s):
        return "60k+"
    if re.search(r"30\s*[-to]+\s*60\s*k?|30k\s*[-_]\s*60k|30\s*to\s*60", s):
        return "30k_60k"
    if re.search(r"15\s*[-to]+\s*30\s*k?|15k\s*[-_]\s*30k|15\s*to\s*30", s):
        return "15k_30k"
    if re.search(r"under\s*15|less\s+than\s+15|below\s+15|<\s*15", s):
        return "under_15k"
    # Single amount → bucket.
    amt = _parse_inr_amount(s)
    if amt is None:
        return None
    if amt < 15_000:
        return "under_15k"
    if amt < 30_000:
        return "15k_30k"
    if amt < 60_000:
        return "30k_60k"
    return "60k+"


def _parse_income_band(text: str) -> Optional[str]:
    """Map free-text income text → one of under_5L / 5L-10L / 10L-25L / 25L+.

    KI-149 (2026-05-15). Same approach as `_parse_budget_band`: explicit
    bucket hints first, then a single rupee amount → bucket.
    """
    if not text:
        return None
    s = str(text).lower()
    if re.search(r"25\s*l\s*\+|>\s*25|more\s+than\s+25|above\s+25|over\s+25", s):
        return "25L+"
    if re.search(r"10\s*[-to]+\s*25\s*l?|10l\s*[-_]\s*25l|10\s*to\s*25", s):
        return "10L-25L"
    if re.search(r"5\s*[-to]+\s*10\s*l?|5l\s*[-_]\s*10l|5\s*to\s*10", s):
        return "5L-10L"
    if re.search(r"under\s*5|less\s+than\s+5|below\s+5|<\s*5", s):
        return "under_5L"
    amt = _parse_inr_amount(s)
    if amt is None:
        return None
    # Income is parsed in rupees; 5 lakh = 500_000.
    if amt < 500_000:
        return "under_5L"
    if amt < 1_000_000:
        return "5L-10L"
    if amt < 2_500_000:
        return "10L-25L"
    return "25L+"


GRAPH: list[Question] = [
    Question(
        id="name",
        prompt_en="First — what should I call you? I'll save your profile so the next time you visit, you won't have to answer all this again.",
        prompt_hi="सबसे पहले — आपको क्या कहूँ? आपकी profile save हो जाएगी, तो अगली बार दोबारा सवाल नहीं पूछूँगा।",
        field="name",
        is_core=True,
        # Free-text — no parser; the orchestrator validates 1-50 chars
    ),
    Question(
        id="age",
        prompt_en="First, your age? (Premium + eligibility + how long you can renew all hinge on this.)",
        prompt_hi="पहला — आपकी उम्र? (Premium और renewal age इसी पर निर्भर हैं।)",
        field="age",
        is_core=True,
        parser=lambda s: int("".join(c for c in str(s) if c.isdigit())[:3] or 0) or None,
    ),
    Question(
        id="dependents",
        prompt_en="Who else needs cover — just you, spouse, kids, parents, or a mix? (Covering parents shifts which policies fit; parent-specific plans often win.)",
        prompt_hi="किसको cover करना है — सिर्फ आप, पति/पत्नी, बच्चे, माता-पिता, या मिलाकर? (माता-पिता के साथ recommendations काफी बदलते हैं।)",
        field="dependents",
        is_core=True,
    ),
    Question(
        id="income_band",
        prompt_en="Annual income band — under ₹5L, ₹5-10L, ₹10-25L, or ₹25L+? (Helps us suggest the right sum-insured size. Not shared with anyone.)",
        prompt_hi="सालाना आय — ₹5L से कम, ₹5-10L, ₹10-25L, या ₹25L+? (हम सिर्फ sum insured size suggest करने के लिए पूछते हैं।)",
        field="income_band",
        is_core=True,
        parser=_parse_income_band,
    ),
    Question(
        id="existing_cover",
        prompt_en="Already have any health insurance — employer-provided or your own? If yes, roughly what sum insured? (If you have ₹5L from work, a top-up plan may fit better than a full base plan.)",
        prompt_hi="पहले से कोई health insurance है — employer का या personal? Sum insured कितना? (Top-up plan ज़्यादा सही हो सकता है।)",
        field="existing_cover_inr",
        is_core=True,
        parser=lambda s: (
            0 if any(k in str(s).lower() for k in ("no", "none", "nothing", "zero", "not"))
            else (
                (int("".join(c for c in str(s) if c.isdigit())[:6] or 0) *
                 (100000 if any(k in str(s).lower() for k in ("l", "lakh", "lac")) else 1))
                or None
            )
        ),
    ),
    Question(
        id="primary_goal",
        prompt_en="What's brought you here — first health policy, upgrading existing cover, comparing specific policies, or tax planning? (Tells us whether to grade you on price, breadth of cover, claim experience, or tax savings.)",
        prompt_hi="आप यहाँ क्यों हैं — पहली policy, upgrade, specific compare, या tax planning? (इससे हम तय करते हैं कि आपको price, coverage, claim experience या tax savings पर grade करें।)",
        field="primary_goal",
        is_core=True,
    ),
    Question(
        id="location",
        prompt_en="Which city? (Cashless hospital network density varies wildly — a '16,000-hospital network' claim means nothing if none are near you.)",
        prompt_hi="कौन सा शहर? (Cashless network आपके शहर में कितना deep है यह बहुत matter करता है।)",
        field="location_tier",
        is_core=True,
    ),
    # Conditional deep-dives
    Question(
        id="parents_age",
        prompt_en="Your parents' ages, and any pre-existing conditions — diabetes, BP, heart, anything chronic? Be honest here: hiding a parent's condition is the #1 reason senior-cover claims get denied later.",
        prompt_hi="माता-पिता की उम्र, और कोई pre-existing condition — diabetes, BP, heart? सच बताइए: hide करना senior-claims denied होने की #1 वजह है।",
        field="parents_age_max",
        condition=lambda p: bool(p.dependents and "parent" in p.dependents.lower()),
    ),
    Question(
        id="health_conditions",
        prompt_en="Any pre-existing conditions on your side — diabetes, BP, thyroid, asthma, anything chronic? Be straight with me here — hiding it lowers your premium ₹500 today and turns into a ₹8 lakh denied claim later when the insurer matches your disclosure against hospital records. Your honest answer protects YOUR claim, not the insurer's profit.",
        prompt_hi="आपकी side से कोई pre-existing condition — diabetes, BP, thyroid? सच बताइए — hide करने से premium तो कम होगा, but claim time पर ₹8 lakh denied हो सकते हैं। आपकी ईमानदारी आपकी claim बचाती है।",
        field="health_conditions",
        condition=_always,
    ),
    Question(
        id="budget",
        prompt_en="Annual premium budget — under ₹15k, ₹15-30k, ₹30-60k, or ₹60k+? (If a slightly higher budget materially improves your protection, I'll flag it.)",
        prompt_hi="Premium के लिए सालाना — ₹15k से कम, ₹15-30k, ₹30-60k, या ₹60k+? (अगर थोड़ा ज़्यादा budget बेहतर protection देगा, बताऊंगा।)",
        field="budget_band",
        is_core=True,
        parser=_parse_budget_band,
    ),
]


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------

def is_field_set(profile: Profile, field_name: str) -> bool:
    v = getattr(profile, field_name, None)
    if v is None:
        return False
    if isinstance(v, (list, str)) and len(v) == 0:
        return False
    return True


def next_question(profile: Profile, language: str = "en") -> Optional[Question]:
    """Return the next question to ask, or None if we have enough to recommend.

    KI-070 (2026-05-15) — the orchestrator no longer drives fact-find via this
    function; the new `backend/fact_find_brain.py` single-LLM-call brain
    handles question phrasing natively. This is now used as a fallback
    (canonical reply when the brain times out / emits malformed JSON), as a
    slot-not-progressing safeguard, and by `/api/profile/completeness` to
    surface "next question hint" to the frontend.
    """
    if profile.free_form_session:
        return None

    for q in GRAPH:
        if q.id in profile.asked:
            continue
        if is_field_set(profile, q.field):
            continue
        cond = q.condition or _always
        if cond(profile):
            return q

    # All applicable questions asked
    return None


def record_answer(profile: Profile, question_id: str, raw_answer: str) -> Profile:
    """Mutate profile in place with a parsed answer."""
    q = next((x for x in GRAPH if x.id == question_id), None)
    if q is None:
        return profile
    value: Any = raw_answer
    if q.parser:
        try:
            value = q.parser(raw_answer)
        except Exception:
            value = None
    if value is not None and value != "":
        setattr(profile, q.field, value)
        # KI-095 — only mark slot asked once setattr succeeds, so a parse
        # failure doesn't leave the slot in an asked-but-empty desync state.
        profile.asked.append(question_id)
    return profile


# ----------------------------------------------------------------------------
# Opportunistic family/dependents extractor — KI-056 (2026-05-15)
# ----------------------------------------------------------------------------
# Real-user testing surfaced: when the user mentions a spouse / kids / parents
# while answering an UNRELATED slot ("my wife also doesn't have anything" in
# response to existing_cover), the bot just acknowledges and moves on without
# capturing the family signal. By the time we reach the dependents slot the
# information has been thrown away. This helper detects family mentions in any
# free-text turn so the orchestrator can pre-fill `profile.dependents`.
#
# Returns one of the canonical `dependents` enum values, or None if no clear
# family signal is present. Conservative on purpose — only the explicit
# combinations are recognised.

_FAMILY_TERM_RE = re.compile(
    r"\b(wife|husband|spouse|partner|kids?|children|child|parents?)\b",
    re.IGNORECASE,
)
_SPOUSE_RE = re.compile(r"\b(wife|husband|spouse|partner)\b", re.IGNORECASE)
_KIDS_RE = re.compile(r"\b(kids?|children|child)\b", re.IGNORECASE)
_PARENTS_RE = re.compile(r"\bparents?\b", re.IGNORECASE)


def infer_dependents_from_text(text: str) -> Optional[str]:
    """Detect spouse/kids/parents mentions in a free-text user message and
    return the matching canonical `dependents` enum value, or None.

    KI-056 (2026-05-15). Used by the orchestrator to pre-fill the dependents
    slot opportunistically when the user volunteers family information while
    answering a different slot.

    Decision tree (in order of specificity):
      - spouse + kids                  → "self+spouse+kids"
      - spouse + parents               → "self+spouse+parents"
      - spouse only                    → "self+spouse"
      - kids only                      → "self+kids"
      - parents only                   → "self+parents"
      - nothing recognised             → None
    """
    if not text or not _FAMILY_TERM_RE.search(text):
        return None
    has_spouse = bool(_SPOUSE_RE.search(text))
    has_kids = bool(_KIDS_RE.search(text))
    has_parents = bool(_PARENTS_RE.search(text))
    if has_spouse and has_kids:
        return "self+spouse+kids"
    if has_spouse and has_parents:
        return "self+spouse+parents"
    if has_spouse:
        return "self+spouse"
    if has_kids:
        return "self+kids"
    if has_parents:
        return "self+parents"
    return None


# KI-068 (2026-05-15) — enum → human-readable labels for the readback
# summary. Previously the bot read back "primary goal: first_buy; metro;
# budget 30k_60k" verbatim, which sounds and reads like a schema dump.
_PRIMARY_GOAL_LABELS = {
    "first_buy": "first health policy",
    "upgrade": "upgrading existing cover",
    "compare_specific": "comparing specific policies",
    "tax_planning": "tax planning",
}
_LOCATION_TIER_LABELS = {
    "metro": "metro city",
    "tier1": "tier-1 city",
    "tier2": "tier-2 city",
    "tier3": "tier-3 city",
}
_BUDGET_BAND_LABELS = {
    "under_15k": "under ₹15,000/year",
    "15k_30k": "₹15,000–30,000/year",
    "30k_60k": "₹30,000–60,000/year",
    "60k+": "₹60,000+/year",
}
_INCOME_BAND_LABELS = {
    "under_5L": "under ₹5L",
    "5_10L": "₹5–10L",
    "10_25L": "₹10–25L",
    "25L+": "₹25L+",
}
_DEPENDENTS_LABELS = {
    "self": "just yourself",
    "self+spouse": "you and your spouse",
    "self+spouse+kids": "you, your spouse, and kids",
    "self+kids": "you and your kids",
    "self+parents": "you and your parents",
    "self+spouse+parents": "you, your spouse, and parents",
    "self+spouse+kids+parents": "you, your spouse, kids, and parents",
}


def readback_summary(profile: Profile) -> str:
    """One-paragraph human-readable summary of the gathered profile.

    KI-068 (2026-05-15) — converts schema-level enum values
    ("first_buy", "30k_60k", "metro") into spoken labels
    ("first health policy", "₹30,000–60,000/year", "metro city") so the
    readback reads like a sentence, not a JSON dump.
    """
    bits = []
    if profile.age:
        bits.append(f"{profile.age} years old")
    if profile.dependents:
        bits.append(f"covering {_DEPENDENTS_LABELS.get(profile.dependents, profile.dependents)}")
    if profile.income_band:
        bits.append(f"income {_INCOME_BAND_LABELS.get(profile.income_band, profile.income_band)}")
    ec = profile.existing_cover_inr
    if isinstance(ec, str):
        try:
            ec = int("".join(c for c in ec if c.isdigit())[:8] or 0)
        except Exception:
            ec = None
    if isinstance(ec, (int, float)):
        bits.append(
            f"existing cover ₹{int(ec):,}"
            if ec > 0
            else "no existing cover"
        )
    if profile.primary_goal:
        bits.append(f"goal: {_PRIMARY_GOAL_LABELS.get(profile.primary_goal, profile.primary_goal)}")
    if profile.location_tier:
        bits.append(_LOCATION_TIER_LABELS.get(profile.location_tier, profile.location_tier))
    if profile.parents_age_max:
        bits.append(f"parents up to age {profile.parents_age_max}")
    if profile.health_conditions:
        hc = profile.health_conditions
        # Defensive: if a string accidentally landed here, wrap it so we don't
        # split it character-by-character in the join. Production hit this on
        # 2026-05-14 — a verbatim STT transcript was stored as a string, then
        # ', '.join(str) emitted "d, i, f, f, e, r, e, n, c, e, ...".
        if isinstance(hc, str):
            hc = [hc] if hc.strip() else []
        if hc:
            bits.append(f"conditions: {', '.join(str(c) for c in hc)}")
    if profile.budget_band:
        bits.append(f"budget {_BUDGET_BAND_LABELS.get(profile.budget_band, profile.budget_band)}")
    return "; ".join(bits) if bits else "(no profile yet)"
