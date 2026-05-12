# Eval Results — 2026-05-12T22:30:15Z

## Headline

| Metric | Value |
| --- | --- |
| Questions run | 25 |
| **Factual accuracy** | **40.0%** |
| **Citation accuracy** | **50.0%** |
| **Refusal precision** | **44.4%** |
| Blocked by faithfulness | 12 |
| Elapsed | 293.0 s |

## By question type

| Type | Accuracy |
| --- | --- |
| coverage_scope | 100.0% |
| regulatory_oos | 66.7% |
| sub_limit | 33.3% |
| exclusions_oos | 33.3% |
| waiting_period | 12.5% |
| bonus | 0.0% |

## By brain (router winners)

| Brain | Accuracy |
| --- | --- |
| groq-llama | 100.0% |
| sarvam-m | 37.5% |

## Sample misses (up to 15)

| id | question | bot_answer | reason |
| --- | --- | --- | --- |
| aditya-birla__activ-assure-diamond__wordings::pre_existing_d | If I have diabetes, how long do I have to wait before I can  | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| aditya-birla__activ-assure-diamond__wordings::room_rent_capp | Is there a cap on room rent under Activ Assure? | The room rent cap under Activ Assure is specified in your Policy Schedule/Produc | BOT does not convey the upgradability to the next level. |
| aditya-birla__activ-assure-diamond__wordings::REFUSE::exclus | Does Activ Assure cover injuries from space tourism? | Activ Assure Diamond does not explicitly cover injuries from space tourism. The  | did not refuse when expected |
| bajaj-allianz__comprehensive-care-plan__wordings::pre_existi | What is the waiting period for pre-existing diseases under C | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__comprehensive-care-plan__wordings::pre_existi | If I have diabetes, how long do I have to wait before I can  | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__comprehensive-care-plan__wordings::initial_wa | What is the initial waiting period under Comprehensive Care  | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__comprehensive-care-plan__wordings::REFUSE::ex | What is the maximum claim amount for diamond-tipped surgical | The Comprehensive Care Plan policy document provided does not mention any specif | did not refuse when expected |
| bajaj-allianz__comprehensive-care-plan__wordings::REFUSE::re | What is the IRDAI mandate on dental coverage that Comprehens | The Comprehensive Care Plan policy document does not explicitly mention any IRDA | did not refuse when expected |
| bajaj-allianz__silver-health__cis::pre_existing_disease_wait | What is the waiting period for pre-existing diseases under S | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__silver-health__cis::pre_existing_disease_wait | If I have diabetes, how long do I have to wait before I can  | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__silver-health__cis::initial_waiting_period_da | What is the initial waiting period under Silver Health? | The initial waiting period under Silver Health (Bajaj Allianz) is **30 days for  | judge_error: HTTPStatusError: Client error '429 Too Many Req |
| bajaj-allianz__silver-health__cis::no_claim_bonus_pct::easy | What's the no-claim bonus on Silver Health? | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__silver-health__cis::room_rent_capping::medium | Is there a cap on room rent under Silver Health? | I'd rather not answer that without stronger evidence in the policy documents I h | bot refused on a question with a known answer |
| bajaj-allianz__silver-health__cis::REFUSE::exclusions_oos::h | Does Silver Health cover injuries from space tourism? | Silver Health by Bajaj Allianz does not explicitly cover injuries from space tou | did not refuse when expected |
| bajaj-allianz__silver-health__cis::REFUSE::exclusions_oos::h | What is the maximum claim amount for diamond-tipped surgical | The provided policy documents do not specifically mention diamond-tipped surgica | did not refuse when expected |

---

*Grader: Groq Llama-3.3-70B-versatile (different model family from Sarvam-M to avoid circular eval).*
*Full per-question results: `eval/results.json`.*
