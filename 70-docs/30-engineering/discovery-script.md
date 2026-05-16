# Discovery Conversation Script

> ⚠️ **Predates the single-brain rewrite — not the present-state map.** Some
> implementation references here (orchestrator / `sales_brain` / 3-tier chain
> / `faithfulness.py` judge) describe code that was removed. Present-state
> authority: [`README.md`](../../README.md) §4. Retained for design intent /
> historical record.

The 10-turn fact-find that turns a stranger into a profiled buyer.

**Design principles** (inspired by Even.in's tone + insurance-sector buyer-research norms):

1. **Plain language.** No "PED", no "UIN", no "sub-limit". We're talking to an adult who hasn't read a policy wording end-to-end.
2. **Every question explains WHY we're asking.** A form feels invasive; a conversation feels helpful. The one-line why-this-matters subtitle is non-optional.
3. **Chips, not text boxes.** Multi-choice + sliders + toggles. Free-text only when no chip set is honest.
4. **Honesty pre-commitment.** Right after the first turn, the bot tells the user: "Tell me the truth, even on hard things. Your honest answer protects your claim later — not just my recommendation."
5. **Optional, not gated.** The user can stop at any time. We surface what we can with partial profiles (insurer-level CSR / complaints) but ONLY personalize the scorecard once `profile_completeness >= 0.6`.

---

## Turn-by-turn script

### Turn 0 — Welcome (the bot starts)

> "Hi — I'm here to help you find a health policy that genuinely fits **you**, not the one that pays the highest commission to a broker. I'll ask about 8–10 short questions. Some will feel personal (your health, what you earn). **Be honest** — every answer is private to this chat, and being upfront about your medical history is the single biggest thing that protects you when you actually need to claim. Ready?"

[Chip: "Let's start" · "Tell me how this works first" · "Just let me browse"]

### Turn 1 — Age

> "What's your age?"
>
> *Why we ask: premium, eligibility, and renewability all hinge on this.*

[Slider 18–80, step 1]

### Turn 2 — Who's covered

> "Who else do you want to cover?"
>
> *Why we ask: covering parents or kids changes which policies make sense — some plans price family floaters very differently.*

[Multi-select chips: "Just me", "Spouse", "Children", "Parents", "Parents-in-law"]

### Turn 3 — Parents' health (CONDITIONAL — only if Turn 2 included parents)

> "If you're covering parents, what's the older one's age, and do they have any pre-existing conditions like diabetes, BP, or heart issues?"
>
> *Why we ask: parents-with-PED need policies with shorter PED waiting periods and lifelong renewability — that narrows the field a lot.*

[Slider for age + chips: "None", "Diabetes", "Hypertension/BP", "Heart", "Cancer", "Thyroid", "Multiple"]

### Turn 4 — Your own conditions

> "Any pre-existing conditions for yourself? Diabetes, BP, thyroid, asthma, anything chronic?"
>
> *Why we ask: this is where honesty matters most. Hiding it gets your premium ₹500 cheaper today and a denied claim of ₹8 lakh later. Insurers can and do find out at claim time.*

[Multi-select: "None", "Diabetes", "BP/Hypertension", "Thyroid", "Asthma", "Heart", "Cancer history", "Other"]

### Turn 5 — Existing cover

> "Do you already have any health insurance — through your employer or that you bought yourself?"
>
> *Why we ask: if you already have ₹5L from work, you might need a top-up rather than a full base plan — different product, different price.*

[Chips: "None", "Employer only", "Personal policy", "Both" → if any, ask sum insured slider]

### Turn 6 — City

> "Which city or town?"
>
> *Why we ask: cashless hospital network density varies massively. A "16,000-hospital network" means nothing if none are near you.*

[Free text + autocomplete] OR [Chips: "Metro", "Tier-1", "Tier-2", "Tier-3 / smaller town"]

### Turn 7 — Budget

> "Roughly what annual premium budget feels comfortable?"
>
> *Why we ask: helps us rank — but if a slightly higher budget materially improves your protection, we'll flag it.*

[Slider with 4 markers: <₹15k, ₹15–30k, ₹30–60k, ₹60k+]

### Turn 8 — Maternity & near-term events (CONDITIONAL)

> "Anything planned in the next 12–24 months — pregnancy, a known surgery, anything you've discussed with a doctor recently?"
>
> *Why we ask: most policies have 30-day initial waits and 24–36-month maternity waits. If you need cover soon, that filters the list.*

[Multi-select: "Pregnancy planned", "Surgery planned", "Recent hospitalisation", "None of these"]

### Turn 9 — Risk preference

> "When it comes to surprises in your bill, what do you prefer?"
>
> *Why we ask: this single answer decides whether co-pay/deductible plans (cheaper premium, share-of-bill) or full-cover plans (higher premium, predictable bill) fit you.*

[Chips: "Lowest premium, I'll accept a 10–20% co-pay", "Balanced", "No surprises — full cover at higher premium"]

### Turn 10 — Income (optional, asked last)

> "One last optional question — your annual income band. We use it only to gauge how much sum insured fits your risk."
>
> *Why we ask: if you earn ₹8L/yr, a ₹50L cover is overkill; if you earn ₹40L/yr, a ₹5L cover leaves you exposed.*

[Chips: "Prefer not to say", "<₹5L", "₹5–10L", "₹10–25L", "₹25L+"]

### Wrap

> "That's all I needed. Here's what I heard: <readback_summary>. I'll now show you 3 policies that fit best, with the exact reasons they ranked well **for you specifically**."

→ Render scorecard cards (now personalised because `profile_completeness >= 0.6`).

---

## Honest disclosure — the trust contract

Right after Turn 0 and again before Turn 4 (own conditions), the bot surfaces a one-line contract:

> "Your answers stay in this conversation. They are NOT shared with any insurer until you choose to buy a policy through their channel. Being honest with me about your medical history is also what makes your claim defensible later — because insurers can match disclosed history against hospital records at claim time."

This is the customer-protection framing. It tells the user honesty is **self-protection**, not insurer-favoring.

---

## Adaptive rules

- If the user is in free-form mode (asks questions back to the bot), don't push the script — let them lead. Resume when they ask "what do you recommend?"
- If `profile_completeness >= 0.6` after some subset of questions, offer to skip the rest: "I have enough to recommend now. Want to keep going, or see what I'd suggest?"
- Never ask the same question twice. `Profile.asked` tracks this.
- A user who says "just show me policies" gets the marketplace with insurer-level metrics (CSR / complaints) visible but per-user scorecards GREYED with a "complete your profile to see how this ranks for you" CTA.

---

## Implementation notes

The 9-slot fact-find SCHEMA still lives in `backend/needs_finder.py::GRAPH` — used now as the schema source for the `sales_brain` LLM system prompt rather than as a scripted question list. Each entry's `id`, `field`, `is_core`, and `condition` are consumed by the LLM as a structured contract; the `prompt_en` / `prompt_hi` strings are no longer rendered to the user post-[ADR-039](../60-decisions/ADR-039-llm-driven-sales-brain.md) / KI-167 (the LLM owns voice + cadence end-to-end via its system prompt + the conversation so far).

To add a new question:

1. Add a `Question(...)` entry with `id`, `field` (which Profile attribute it sets), `is_core` (boolean — counts toward completeness), optional `condition` callable, optional `parser`.
2. Surface the new slot in the `sales_brain` system prompt's 9-slot schema (alongside accepted value shapes + examples) so the LLM knows to capture it. See `backend/sales_brain.py::_SYSTEM_PROMPT`.
3. Wire any post-capture validation into `backend/sales_brain_normalizer.py` (enum coercion, INR parsing, bounds).
4. Add a row in `70-docs/scorecard-knowledge-graph.md` Part B showing how the new input shifts weights.
5. Wire the shift into `_profile_tuned_weights()` in `backend/scorecard.py`.

Drift between these places breaks the transparency promise. Keep them in sync.
