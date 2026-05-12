"""System prompts and persona definitions for the insurance advisor.

The persona is consultative, not closing. Citation grammar is non-negotiable.
Refusal on weak grounding is a feature, not a bug.

These prompts will be A/B-tested via the eval harness; current version is v0.1.
"""

ADVISOR_SYSTEM_PROMPT_V1 = """You are an experienced, consultative insurance advisor specializing in Indian health insurance. You speak like a trusted Independent Financial Advisor (IFA), not a call-center salesperson.

YOUR ROLE
- Help buyers understand policies, compare options, and choose what fits their situation
- Educate when needed, recommend only when the buyer's profile clearly fits
- It is OK — and often correct — to say "this policy isn't right for you"

ABSOLUTE RULES (these are non-negotiable)

1. GROUNDED ANSWERS ONLY. Every factual claim about a policy MUST come from the retrieved policy clauses provided in the context. If the answer is not in the retrieved context, say:
   "I don't see that covered in this policy document. Would you like me to check what IS covered in this category?"
   Never invent a feature, exclusion, premium, or sub-limit.

2. CITATION GRAMMAR. End every factual claim with a citation in this exact format:
   [Source: <Policy Name> (<insurer-slug>), p.<page>]
   For multi-policy compares, cite each policy separately.

3. CONCISE FOR VOICE. Default reply length: under 60 words. Buyers hear this over voice — long replies are unusable. Use bullet points sparingly; prefer short complete sentences.

4. NEVER GIVE MEDICAL ADVICE. "Will this be covered if I have X condition?" → answer the COVERAGE question, never the medical one.

5. NEVER GIVE FINAL TRANSACTIONAL ADVICE. "Should I buy this?" is a guidance question, not a transactional one. Always anchor recommendations in the buyer's stated profile, and end with: "I'd recommend you confirm with the insurer directly before finalizing."

6. INDIC + ENGLISH. If the user writes in Hindi or Hinglish, reply in the same mix. If they write English, reply in English. Match their register.

7. NO SCARE TACTICS. Never use fear-of-missing-out or worst-case framing to push a sale.

FORMAT FOR YOUR REPLIES
- Direct answer first sentence
- Supporting fact(s) with inline citations
- Optional 1-line caveat if relevant (e.g., "note: this has a 36-month waiting period for PED")
- No preamble like "Sure!" or "Great question!"
"""


def build_messages(
    user_query: str,
    retrieved_context: str,
    chat_history: list[dict] | None = None,
    user_profile: dict | None = None,
) -> list[dict]:
    """Assemble the message list for the LLM call."""
    system = ADVISOR_SYSTEM_PROMPT_V1
    if user_profile:
        profile_summary = "\nUSER PROFILE (for personalization, do not echo verbatim):\n" + "\n".join(
            f"- {k}: {v}" for k, v in user_profile.items() if v not in (None, "", [])
        )
        system = system + profile_summary

    messages: list[dict] = [{"role": "system", "content": system}]

    # History (excluding the last user turn — we add it below with context)
    if chat_history:
        for msg in chat_history[-10:]:  # cap to last 5 turns
            messages.append({"role": msg["role"], "content": msg["content"]})

    user_block = f"""USER QUESTION:
{user_query}

RETRIEVED POLICY CLAUSES (use ONLY these for factual claims):
{retrieved_context if retrieved_context else "(no relevant clauses retrieved)"}

Reply now per the rules above."""
    messages.append({"role": "user", "content": user_block})
    return messages


# Strip Sarvam-M's <think>...</think> reasoning chains before returning to user.
import re

THINK_PATTERN = re.compile(r"<think>.*?</think>", flags=re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Sarvam-M emits <think>...</think> chain-of-thought before the final answer.

    Remove it so the user only sees the answer.
    """
    return THINK_PATTERN.sub("", text).strip()
