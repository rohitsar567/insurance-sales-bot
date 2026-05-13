# Eval Results — 2026-05-13T21:52:02Z

## Headline

| Metric | Value |
| --- | --- |
| Questions run | 10 |
| **Factual accuracy** | **30.0%** |
| **Citation accuracy** | **100.0%** |
| **Refusal precision** | **33.3%** |
| Blocked by faithfulness | 0 |
| Elapsed | 428.7 s |

## By question type

| Type | Accuracy |
| --- | --- |
| coverage_scope | 100.0% |
| regulatory_oos | 100.0% |
| waiting_period | 20.0% |
| sub_limit | 0.0% |
| exclusions_oos | 0.0% |

## By brain (router winners)

| Brain | Accuracy |
| --- | --- |
| nim | 30.0% |

## Sample misses (up to 15)

| id | question | bot_answer | reason |
| --- | --- | --- | --- |
| aditya-birla__activ-assure-diamond__wordings::pre_existing_d | What is the waiting period for pre-existing diseases under A | The waiting period for pre-existing diseases under Activ Assure Diamond is not e | BOT mentions a 48-month waiting period for specific conditio |
| aditya-birla__activ-assure-diamond__wordings::pre_existing_d | If I have diabetes, how long do I have to wait before I can  | For diabetes, Activ Assure Diamond has a standard waiting period for pre-existin | BOT does not provide the specific waiting period duration fo |
| aditya-birla__activ-assure-diamond__wordings::room_rent_capp | Is there a cap on room rent under Activ Assure? | Activ Assure does not have a fixed rupee cap on room rent, but it defines eligib | BOT provides additional info but misses the key detail about |
| aditya-birla__activ-assure-diamond__wordings::REFUSE::exclus | Does Activ Assure cover injuries from space tourism? | Activ Assure Diamond doesn't explicitly cover or exclude injuries from space tou | did not refuse when expected |
| aditya-birla__activ-assure-diamond__wordings::REFUSE::exclus | What is the maximum claim amount for diamond-tipped surgical | The policy document does not mention any specific maximum claim amount for diamo | did not refuse when expected |
| bajaj-allianz__comprehensive-care-plan__wordings::pre_existi | What is the waiting period for pre-existing diseases under C | The Comprehensive Care Plan has a 90-day initial waiting period from policy comm | BOT incorrectly states the waiting period for pre-existing d |
| bajaj-allianz__comprehensive-care-plan__wordings::pre_existi | If I have diabetes, how long do I have to wait before I can  | Diabetes is considered a pre-existing disease under this policy, so claims relat | BOT incorrectly states waiting period and exclusion details  |

---

*Grader: Groq Llama-3.3-70B-versatile (different model family from Sarvam-M to avoid circular eval).*
*Full per-question results: `eval/results.json`.*
