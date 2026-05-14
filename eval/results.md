# Eval Results — 2026-05-14T10:56:52Z

## Headline

| Metric | Value |
| --- | --- |
| Questions run | 5 |
| **Factual accuracy** | **60.0%** |
| **Citation accuracy** | **100.0%** |
| **Refusal precision** | **0.0%** |
| Blocked by faithfulness | 0 |
| Elapsed | 62.3 s |

## By question type

| Type | Accuracy |
| --- | --- |
| waiting_period | 100.0% |
| sub_limit | 100.0% |
| coverage_scope | 0.0% |
| exclusions_oos | 0.0% |

## By brain (router winners)

| Brain | Accuracy |
| --- | --- |
| nim-chain | 60.0% |

## Sample misses (up to 15)

| id | question | bot_answer | reason |
| --- | --- | --- | --- |
| aditya-birla__activ-assure-diamond__wordings::ayush_coverage | Does Activ Assure cover AYUSH (Ayurveda, Yoga, Unani, Siddha | Yes, Activ Assure covers AYUSH treatments under in-patient hospitalization, incl | regex: no_overlap (gold_words=[]) |
| aditya-birla__activ-assure-diamond__wordings::REFUSE::exclus | Does Activ Assure cover injuries from space tourism? | Activ Assure does not cover injuries from space tourism. The policy excludes par | did not refuse when expected |

---

*Grader: Groq Llama-3.3-70B-versatile (different model family from Sarvam-M to avoid circular eval).*
*Full per-question results: `eval/results.json`.*
