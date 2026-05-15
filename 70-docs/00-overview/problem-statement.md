# 01 — Requirements

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Document version | 0.1 (draft) |
| Date | 2026-05-13 |
| Author | Rohit Saraf |
| Status | In review — pending stakeholder sign-off |
| Reviewer | (to be assigned) |
| Source brief | Sarvam AI take-home assignment |

---

## 0. Document purpose

This is the foundational requirements document for the Insurance Sales Portfolio Expert. It defines what we are building, for whom, against what measurable success criteria, and — equally important — what we are explicitly **not** building.

All downstream documents (`02-architecture.md`, `03-eval-plan.md`, `04-failure-modes.md`, `05-needs-analysis-flow.md`, `decisions.md`) depend on this spec. Changes here cascade; this is the source of truth for product scope.

This document was authored with deliberate pre-implementation rigour because the assignment is evaluated as much on the clarity of the design narrative as on the working bot. Where decisions in this doc rule things out, the rationale is captured inline so a future reader (or interviewer) can audit the choices.

---

## 1. Product vision (one paragraph)

A **voice-first AI advisor for Indian health insurance buyers**, deployed over a **curated, pre-acquired corpus** of policy documents from leading Indian health insurers. The product replaces the typical experience of speaking to a generalist advisor at a horizontal aggregator — adaptive needs discovery, granular policy filtering, side-by-side comparison, illustrative pricing, and consultative recommendation — and surfaces all of it through a natural, code-switched (English + Hindi) voice conversation grounded in the actual policy wordings, with clause-level citations on every factual claim.

### 1.1 Strategic framing (for Sarvam)

The product is **consumer-facing in experience** but **B2B in commercial application**. The realistic deployment is an insurer or aggregator white-labelling this advisor to assist customers shopping for health insurance, with Sarvam's ASR / TTS / Indic LLM stack as the core enabler. The assignment build is the consumer surface; the architecture must remain coherent for a white-labelled / multi-tenant future.

---

## 2. Differentiation

The advisor experience that exists today in India is split: aggregators (PolicyBazaar, Coverfox, InsuranceDekho) provide structured comparison but lean heavily on human callbacks for actual advisory; bank / insurer-direct channels provide depth but only on their own products. Both surfaces struggle with voice as a first-class modality and with grounding recommendations in primary source documents.

| Dimension | Status quo (aggregator + callback) | This product |
| --- | --- | --- |
| Primary interface | Web form → human callback | Voice-native, AI-led end-to-end |
| Policy depth | Marketing summaries + feature ladders | Full policy wordings + clause-level cited Q&A |
| Recommendation basis | Quote price + commercial incentive | Buyer profile + policy fit, transparently reasoned |
| Indic language support | English primary, Hindi shallow | Hindi / Hinglish first-class through conversation |
| Trust mechanism | Brand + reviews + advisor rapport | Citation grammar — every factual claim links to source clause |
| Marginal cost-to-serve | Salaried human advisor per buyer | Voice agent, marginal cost ≈ STT + LLM + TTS per turn |
| Persistence of advice | Voice call, then lost | Replayable transcript with cited recommendations |

The point of differentiation is **not "no humans"** — it is **"depth + grounding + Indic-native + low marginal cost"** in a single surface. Human escalation remains a designed-in path (see §6 non-goals).

---

## 3. User personas

Three personas, intentionally spanning the spectrum of prior knowledge and intent. The product must serve all three without forcing them into a single flow.

### 3.1 Persona A — Priya, 28, Pune | first-time buyer

- Software professional, two years into career, no dependants yet.
- Triggered by a friend's recent hospitalisation and a ₹4L out-of-pocket bill.
- Has **no current policy** beyond a basic employer cover she doesn't fully understand.
- Doesn't know what to ask — needs the advisor to **lead the conversation** and educate as it goes.
- Budget-sensitive (₹10–20K annual premium acceptable).
- Comfortable in **Hindi-English code-switch**, prefers conversational voice over reading.
- Success looks like: she ends the session with a clear shortlist of 2 policies, understands what each covers, and knows what she's paying for.

### 3.2 Persona B — Anjali, 32, Mumbai | informed shopper

- Senior engineer at a growth-stage startup, recently married, no kids yet.
- Has employer cover (₹5L floater) and worries it is insufficient and not portable.
- Has heard of HDFC ERGO, Star, Niva Bupa — wants help choosing between **2–3 specific policies** she's already aware of.
- Time-poor — wants a guided, voice-led experience during her commute.
- Comfortable in English; will tolerate Hindi if natural.
- Success looks like: she gets a defensible comparison across her shortlist, surfaces sub-limits and waiting periods she hadn't thought to ask about, and walks away ready to buy.

### 3.3 Persona C — Vikram, 45, Bengaluru | family decision-maker

- Mid-career VP, two school-age kids, ageing parents (70 and 68) at home.
- Already has a family floater; explicitly evaluating **a separate parent policy** (high-sum-insured, senior-citizen plan).
- Knows the basics; wants to **deep-dive into sub-limits, claim settlement ratios, network hospitals, room-rent capping, and pre-existing-disease waiting periods**.
- Will press the advisor on specific clauses — must be answered with citations, not vibes.
- English-dominant.
- Success looks like: he extracts decision-grade information without having to read three 60-page PDFs himself, and is shown the trade-offs explicitly.

All three personas share one trait: **they do not want to be sold to.** They want to be advised. The product persona (§5.2) reflects this.

---

## 4. Buyer journey

The advisor flow is **adaptive** — branches expand or contract based on conversational signal — but the seven-stage backbone is stable:

```
  ┌────────────────────────┐
  │ 1. Greeting + framing  │  "I'm an advisor, not a broker. I'll ask you a few
  └────────────┬───────────┘   things, then we'll look at policies together."
               │
  ┌────────────▼───────────┐
  │ 2. Adaptive fact-find  │  Universal core: age, dependants, income, existing
  │    (see Doc 05)        │   cover, primary goal, location. Conditional deep
  └────────────┬───────────┘   dives based on signal.
               │
  ┌────────────▼───────────┐
  │ 3. Profile readback    │  "Here's what I've understood. Correct me." User
  └────────────┬───────────┘   confirms or amends; agent commits the profile.
               │
  ┌────────────▼───────────┐
  │ 4. Shortlist           │  Agent applies hard filters (must-haves) +
  │    generation          │   soft scoring to corpus; returns 3–5 candidates.
  └────────────┬───────────┘
               │
  ┌────────────▼───────────┐
  │ 5. Side-by-side        │  Visual comparison surface in the UI; advisor
  │    comparison          │   narrates differences in voice.
  └────────────┬───────────┘
               │
  ┌────────────▼───────────┐
  │ 6. Recommendation +    │  Advisor recommends one or two with reasoning
  │    transparent why     │   tied back to the user's profile.
  └────────────┬───────────┘
               │
  ┌────────────▼───────────┐
  │ 7. Open Q&A + handoff  │  Free-form voice Q&A with clause-level citations.
  └────────────────────────┘   Optional: handoff path to a human / insurer.
```

The conversation may revisit earlier stages — for example, Vikram (Persona C) may skip 1–3 and jump straight to 5/7 by saying "compare these three for me." The architecture must permit non-linear traversal.

---

## 5. Product principles

These are the non-negotiable principles that shape every downstream design decision. Each is stated as a rule, with the reason it exists.

### 5.1 Grounded over fluent

If we cannot cite the source for a factual claim, we do not make the claim. The advisor will say "I don't see that in the policy document" before it improvises. Hallucinations on coverage details are not "small UX bugs" — they are mis-selling, which is regulated in India and a reputational risk for any commercial deployment.

### 5.2 Consultative, never closing

The advisor's persona is modelled on a good Independent Financial Advisor — informed, patient, comfortable saying "this isn't the right policy for you." It is explicitly **not** a call-centre closer. Persuasion is allowed once the buyer signals readiness; manipulation is not.

### 5.3 Indic-native, not translated

Hindi and Hinglish are designed-in from day one, not retrofitted. The advisor opens by detecting the user's language preference within the first one or two turns and adapts.

### 5.4 Curated corpus is the product's moat

The user does not upload PDFs. We pre-acquire, normalise, and structurally extract from the corpus so quality is under our control. The corpus is the product's defensibility relative to a generic RAG-over-anything bot.

### 5.5 Architecture is built for category expansion from day one

Scope v1 is health insurance only, but every schema, interface, and adapter is designed so adding Life / Motor / Travel later is a configuration change, not a refactor. See §7 (Constraints) and Doc 02 (Architecture) for the seven c-readiness commitments.

### 5.6 Explainability is a feature, not a chore

The architecture document, the decisions log, and the eval harness are first-class deliverables, not afterthoughts. A reviewer should be able to walk through any technology pick and find the alternatives we considered and the empirical basis for the choice.

---

## 6. Success criteria

What "working" means, measurably. Every entry here will have a corresponding test in `03-eval-plan.md`.

| # | Criterion | Threshold for v1 | How measured |
| --- | --- | --- | --- |
| C1 | End-to-end voice latency (user speech end → advisor speech start) | **p50 ≤ 4s, p95 ≤ 7s** | Instrumented turn logs |
| C2 | Factual answer accuracy on gold Q&A set | **≥ 95% correct** | Automated grader vs. 100-question gold set per policy |
| C3 | Citation accuracy (cited clause actually supports the claim) | **≥ 95%** | Automated grader on cited spans |
| C4 | Refusal precision on out-of-policy questions | **≥ 90%** (refuses when it should) | Adversarial test set in eval plan |
| C5 | Structured-field extraction accuracy from PDFs | **≥ 95% per field, ≥ 90% per policy across all fields** | Manual gold-labelled subset (5 policies × all fields) |
| C6 | Comparison view correctness | **100% of compared fields match the structured store** | Snapshot test on comparison rendering |
| C7 | Pricing band realism | **Illustrative band within ±25% of a real PolicyBazaar quote** for 10 spot checks | Manual ground truth |
| C8 | Hindi/Hinglish handling | **No degradation > 5pp on C2/C3** when user speaks Hindi vs. English | Bilingual gold set |
| C9 | Recommendation defensibility | **Reasoning trace explicitly references the user's profile fields** in 100% of recommendations | Human review of 30 sessions |
| C10 | UI polish (qualitative) | Demo-grade — fonts harmonised, no console errors, no broken states | Internal review checklist |

These thresholds are deliberately aggressive. Where we fall short, the doc will record the actual numbers and why — that honesty is itself part of the artifact.

---

## 7. Non-goals (explicit)

To protect against scope creep — and to give the interviewer something to grade against — these are explicitly **out of scope** for v1.

- **Transacting / selling.** We do not collect payment, issue policies, or integrate with insurer KYC. Handoff to the insurer is a link, not a flow.
- **Underwriting.** We do not assess whether the user *will* be granted a policy at a given price. We surface illustrative pricing only.
- **Medical advice.** "Will this treatment be covered" is in scope. "Should I get this treatment" is not.
- **Real-time quote pulls from insurer APIs.** Out of scope; possibly out of reach commercially. We use aggregated public data and label pricing as illustrative.
- **Categories beyond Health.** Life, Motor, Travel, Cyber, etc. are explicitly v2. The architecture accommodates them; we do not populate them.
- **Multi-user / multi-tenant deployment.** v1 is single-user demo. Stateless services + canonical DB make v2 multi-tenant tractable.
- **User-uploaded PDFs.** Corpus is curated. (See §5.4.)
- **Mobile-native deployment.** v1 is desktop browser. Mobile responsive is a stretch goal.
- **Real-time co-browsing with a human advisor.** v2 handoff feature.
- **Persistent user accounts across sessions.** v1 is session-scoped.
- **Persona-A onboarding video / tutorial layer.** The advisor itself does the onboarding through voice.

---

## 8. Constraints

### 8.1 Time

- Sarvam assignment deadline: **TBD — pending confirmation.** Working assumption: 2 weeks from kick-off.

### 8.2 Stack expectations (Sarvam-aware)

- We will **benchmark Sarvam's STT, TTS, and Indic LLM offerings** against best-in-class alternatives on a real test set. Picks are justified in `decisions.md`. Silent defaults to OpenAI / Anthropic / ElevenLabs are not acceptable.
- We will use English-only PDFs as input (regulator-mandated language for policy wordings is English).
- The conversational layer must support Hindi and Hinglish output regardless of input language.

### 8.3 Regulatory / ethical

- No mis-selling. The advisor must not push a product that the user's profile does not fit.
- No medical advice.
- All pricing is labelled "illustrative" with a visible disclaimer and source citation.
- No personally identifiable information persisted beyond the session.
- Document sourcing: only publicly available brochures, customer information sheets, and policy wordings from insurer websites and IRDAI's product database. No scraping behind logins.

### 8.4 Technical envelope

- v1 UI: Next.js 14 (App Router) + Tailwind v4 + shadcn/ui, calling a FastAPI backend. (The original draft scoped Streamlit; the migration to Next.js + FastAPI is captured in [ADR-005](../60-decisions/ADR-005-nextjs-fastapi-frontend.md).)
- v1 corpus: 15–20 health policies across 5 leading Indian insurers (Star Health, HDFC ERGO, Niva Bupa, Care Health, ICICI Lombard — exact list pending availability check).
- v1 deployment: local development + a single-instance deploy on Hugging Face Spaces (Docker) for the interviewer to demo.

---

## 9. Open questions (deferred to downstream docs)

These are intentionally **not** resolved here. Each is owned by a specific later document.

| # | Question | Owned by |
| --- | --- | --- |
| Q1 | Exact STT / TTS / LLM / embedding provider picks, with benchmarks | `02-architecture.md` + `decisions.md` |
| Q2 | Structured schema — exact 30–50 field set, field types, optionality | `02-architecture.md` |
| Q3 | Extraction pipeline — single-pass LLM vs. self-critique vs. human-in-loop | `02-architecture.md` |
| Q4 | Gold Q&A set construction — sources, size, grader design | `03-eval-plan.md` |
| Q5 | Refusal taxonomy — exact categories and example utterances | `03-eval-plan.md` + `04-failure-modes.md` |
| Q6 | Fact-find question graph — nodes, edges, termination criteria | `05-needs-analysis-flow.md` |
| Q7 | Pricing aggregation sources, freshness policy, ±25% empirical proof | `02-architecture.md` |
| Q8 | Recommendation engine — rule-based pre-filter vs. pure-LLM reasoning | `02-architecture.md` |
| Q9 | Failure modes — full register with mitigations | `04-failure-modes.md` |
| Q10 | Observability — what we log per turn, retention, dashboards | `02-architecture.md` |

---

## 10. Revision history

| Version | Date | Author | Change |
| --- | --- | --- | --- |
| 0.1 | 2026-05-13 | Rohit Saraf | Initial draft post-brainstorm with AI architect |

---

## 11. Sign-off

This document is in review. Approval required from:

- [ ] Rohit Saraf (project owner)

Once approved, this doc becomes the source-of-truth spec. Subsequent docs reference it by section number.
