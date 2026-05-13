// Lightweight i18n for the visible chrome. Covers hero, buttons, criterion
// names, marketplace filters, modal headings — the strings a buyer SEES
// before deep prose. Long policy descriptions stay in their source language.
//
// Usage:
//   const { lang, setLang, t } = useLang();
//   t("hero.title")  // "Find a health policy that genuinely fits you."

export type UILang = "en" | "hi";

export const UI_STRINGS = {
  en: {
    // Header
    "header.title": "Health insurance, finally honest.",
    "header.subtitle": "Compare. Score. Buy what fits — not what pays the highest commission.",
    "header.policy_library": "Policy Library",
    "header.policy_library_kicker": "Interactive",
    "header.annual_premium": "Annual premium",
    "header.annual_premium_kicker": "Estimate",
    "header.policies_label": "POLICIES",
    "header.insurers_label": "INSURERS",

    // Welcome / empty state
    "welcome.heading_a": "Find a health policy that genuinely fits ",
    "welcome.heading_b": "you",
    "welcome.heading_c": ".",
    "welcome.subtitle": "I'll ask 8–10 short questions, then show you 3 policies that match — with the exact reasons each ranked well.",
    "welcome.no_commissions": "No broker commissions in my ranking.",
    "welcome.source_link": "Every fact you see has a source link.",
    "welcome.trust_title": "Tell me the truth — even on the hard things.",
    "welcome.trust_body": "When I ask about your health later, please don't hide a condition to lower your premium. Insurers cross-check disclosed history against hospital records at claim time. The ₹500/month you save today turns into an ₹8 lakh denied claim later. Your honest answers stay in this chat — they're not shared with any insurer until you choose to buy.",
    "welcome.coverage_template": "${policies} policies across ${insurers} insurers indexed. Or upload your own policy PDF — I'll analyse it the same way.",

    // Input bar
    "input.placeholder": "Ask about coverage, waiting periods, exclusions, or compare policies…",
    "input.send": "Send",
    "input.voice_reply": "Voice reply",
    "input.hands_free": "Hands-free",
    "input.lang_label": "Lang:",
    "input.voice_input": "Voice input",
    "input.upload": "Upload your own policy PDF",
    "input.enter_to_send": "Enter to send · 📎 to upload your own PDF",

    // Marketplace panel
    "mp.heading": "Health insurance marketplace",
    "mp.summary": "${total} policies from ${insurers} leading Indian health insurers. Click any policy for the full rating, key terms, and the source document.",
    "mp.close": "close",
    "mp.search": "SEARCH",
    "mp.search_placeholder": "Policy or insurer name…",
    "mp.insurer": "INSURER",
    "mp.all_insurers": "All",
    "mp.min_rating": "MIN RATING",
    "mp.all_grades": "All grades",
    "mp.a_only": "A only",
    "mp.b_or_better": "B or better",
    "mp.c_or_better": "C or better",
    "mp.sort_by": "SORT BY",
    "mp.sort_score": "Highest rated",
    "mp.sort_name": "Policy name (A–Z)",
    "mp.sort_insurer": "Insurer (A–Z)",
    "mp.max_ped_wait": "MAX PRE-EXISTING WAIT:",
    "mp.min_sum_insured": "MIN SUM INSURED:",
    "mp.ayush_covered": "AYUSH covered",
    "mp.cashless_network": "Cashless network",
    "mp.showing": "Showing",
    "mp.of": "of",
    "mp.policies_word": "policies",
    "mp.no_match": "No policies match these filters. Try widening the criteria.",
    "mp.compare": "Compare",
    "mp.selected": "Selected",
    "mp.clear": "Clear",
    "mp.compare_count": "of ${max} selected",

    // Policy card stat labels
    "stat.sum_insured_up_to": "Sum insured up to",
    "stat.ped_waiting": "PED waiting",
    "stat.ayush": "AYUSH",
    "stat.network": "Network",

    // Detail modal
    "detail.policy_pdf": "Policy PDF",
    "detail.find_pdf": "Find PDF",
    "detail.key_terms": "Key terms",
    "detail.entry_age": "Entry age",
    "detail.renewal_up_to": "Renewal up to",
    "detail.initial_waiting": "Initial waiting",
    "detail.pre_existing_waiting": "Pre-existing waiting",
    "detail.maternity_waiting": "Maternity waiting",
    "detail.copayment": "Copayment",
    "detail.no_claim_bonus": "No-claim bonus",
    "detail.network_hospitals": "Network hospitals",
    "detail.ayush_covered": "AYUSH covered",
    "detail.maternity": "Maternity",
    "detail.cashless": "Cashless",
    "detail.room_rent": "Room rent",
    "detail.generic_grade_title": "This is the generic grade for an average buyer.",
    "detail.generic_grade_body": "Tell me about yourself (age, dependents, conditions, budget) and I'll re-score this policy for your situation. The same policy can be a B for a 30-year-old and a D for a 60-year-old with diabetes — context changes everything.",
    "detail.personalized_label": "Personalized for you · profile",
    "detail.profile_complete": "complete",
    "detail.methodology_q": "How is this score computed?",
    "detail.methodology_sub": "(48 fields → 6 criteria, with weights)",

    // Scorecard criterion blurbs
    "scorecard.coverage_blurb": "What's actually covered when you claim",
    "scorecard.cost_blurb": "How likely you'll face surprise out-of-pocket bills",
    "scorecard.waiting_blurb": "How soon you can actually use the policy",
    "scorecard.claim_blurb": "Will the insurer actually pay when you claim?",
    "scorecard.renewal_blurb": "Can you keep this policy at 70+ when you need it most",
    "scorecard.bonus_blurb": "Rewards for staying claim-free + renewing",
    "scorecard.weighted_avg": "Weighted average across 6 criteria. Rules-based — no LLM in the scoring loop.",

    // Footer
    "footer.disclaimer": "Advisory only. Information based on policy documents; verify with the insurer before purchase. All policy ratings are illustrative and based on publicly disclosed data.",

    // Suggested questions
    "suggested.q1": "I'm looking for a new health insurance policy.",
    "suggested.q2": "What is the waiting period for pre-existing diseases?",
    "suggested.q3": "Does HDFC ERGO Optima Secure cover AYUSH?",
    "suggested.q4": "What's the room rent cap on Care Supreme?",

    // Grade one-liners (mirror backend/scorecard.py::grade_for)
    "grade.a": "Strong all-rounder — solid pick for the buyer.",
    "grade.b": "Good policy with a few notable gaps.",
    "grade.c": "Decent baseline; check the trade-offs before signing.",
    "grade.d": "Material concerns — only suitable for specific use-cases.",
    "grade.f": "Significant gaps — alternative options are likely better.",

    "card.see_score_pill": "See score",
    "card.see_score_sub": "build profile",
    "card.score_locked_msg": "Complete your profile and I'll score this policy for you.",
  },
  hi: {
    "header.title": "स्वास्थ्य बीमा, अब ईमानदारी से।",
    "header.subtitle": "तुलना करें। स्कोर देखें। वो खरीदें जो आपके लिए सही है — कमीशन वाला नहीं।",
    "header.policy_library": "पॉलिसी लाइब्रेरी",
    "header.policy_library_kicker": "इंटरेक्टिव",
    "header.annual_premium": "वार्षिक प्रीमियम",
    "header.annual_premium_kicker": "अनुमान",
    "header.policies_label": "पॉलिसियाँ",
    "header.insurers_label": "बीमाकर्ता",

    "welcome.heading_a": "आपके लिए सही स्वास्थ्य पॉलिसी खोजें — ",
    "welcome.heading_b": "बस आपके लिए",
    "welcome.heading_c": "।",
    "welcome.subtitle": "मैं 8–10 छोटे सवाल पूछूंगा, फिर 3 पॉलिसियाँ दिखाऊंगा जो आपके लिए सबसे सही हैं — सटीक कारणों के साथ।",
    "welcome.no_commissions": "मेरी रैंकिंग में कोई ब्रोकर कमीशन नहीं।",
    "welcome.source_link": "हर तथ्य का source link है।",
    "welcome.trust_title": "सच बताइए — मुश्किल बातें भी।",
    "welcome.trust_body": "जब मैं आपकी सेहत के बारे में पूछूं, premium कम करने के लिए कोई condition मत छिपाइए। बीमाकर्ता claim time पर hospital records से check करते हैं। आज के ₹500/महीने की बचत बाद में ₹8 लाख का denied claim बन जाती है। आपके ईमानदार जवाब इसी chat में रहते हैं — किसी insurer के साथ शेयर नहीं होते।",
    "welcome.coverage_template": "${policies} पॉलिसियाँ, ${insurers} बीमाकर्ताओं से indexed हैं। अपनी policy PDF भी upload कर सकते हैं।",

    "input.placeholder": "Coverage, waiting period, exclusion, या तुलना के बारे में पूछिए…",
    "input.send": "भेजें",
    "input.voice_reply": "आवाज़ में जवाब",
    "input.hands_free": "हैंड्स-फ्री",
    "input.lang_label": "भाषा:",
    "input.voice_input": "आवाज़ input",
    "input.upload": "अपनी policy PDF upload करें",
    "input.enter_to_send": "Enter दबाकर भेजें · 📎 से PDF upload",

    "mp.heading": "स्वास्थ्य बीमा बाज़ार",
    "mp.summary": "${total} पॉलिसियाँ, ${insurers} प्रमुख भारतीय बीमाकर्ताओं से। पूरी रेटिंग और source document के लिए किसी भी पॉलिसी पर click करें।",
    "mp.close": "बंद करें",
    "mp.search": "खोजें",
    "mp.search_placeholder": "पॉलिसी या बीमाकर्ता का नाम…",
    "mp.insurer": "बीमाकर्ता",
    "mp.all_insurers": "सभी",
    "mp.min_rating": "न्यूनतम रेटिंग",
    "mp.all_grades": "सभी ग्रेड",
    "mp.a_only": "केवल A",
    "mp.b_or_better": "B या बेहतर",
    "mp.c_or_better": "C या बेहतर",
    "mp.sort_by": "क्रम लगाएँ",
    "mp.sort_score": "सर्वोच्च रेटेड",
    "mp.sort_name": "पॉलिसी नाम (A–Z)",
    "mp.sort_insurer": "बीमाकर्ता (A–Z)",
    "mp.max_ped_wait": "अधिकतम PED प्रतीक्षा:",
    "mp.min_sum_insured": "न्यूनतम बीमित राशि:",
    "mp.ayush_covered": "AYUSH कवर",
    "mp.cashless_network": "कैशलेस नेटवर्क",
    "mp.showing": "दिखा रहा है",
    "mp.of": "/",
    "mp.policies_word": "पॉलिसियाँ",
    "mp.no_match": "इन filters से कोई पॉलिसी match नहीं। Criteria widen करके देखें।",
    "mp.compare": "तुलना",
    "mp.selected": "चयनित",
    "mp.clear": "साफ़ करें",
    "mp.compare_count": "${max} में से चयनित",

    "stat.sum_insured_up_to": "बीमित राशि तक",
    "stat.ped_waiting": "PED प्रतीक्षा",
    "stat.ayush": "AYUSH",
    "stat.network": "नेटवर्क",

    "detail.policy_pdf": "पॉलिसी PDF",
    "detail.find_pdf": "PDF खोजें",
    "detail.key_terms": "मुख्य शर्तें",
    "detail.entry_age": "प्रवेश आयु",
    "detail.renewal_up_to": "नवीनीकरण तक",
    "detail.initial_waiting": "प्रारंभिक प्रतीक्षा",
    "detail.pre_existing_waiting": "Pre-existing प्रतीक्षा",
    "detail.maternity_waiting": "मातृत्व प्रतीक्षा",
    "detail.copayment": "Co-payment",
    "detail.no_claim_bonus": "No-claim bonus",
    "detail.network_hospitals": "Network hospitals",
    "detail.ayush_covered": "AYUSH cover",
    "detail.maternity": "मातृत्व",
    "detail.cashless": "कैशलेस",
    "detail.room_rent": "Room rent",
    "detail.generic_grade_title": "यह औसत खरीदार के लिए सामान्य ग्रेड है।",
    "detail.generic_grade_body": "अपने बारे में बताइए (उम्र, dependents, conditions, budget) और मैं इस policy को आपके लिए re-score करूंगा। एक ही पॉलिसी 30 साल वाले के लिए B हो सकती है और 60 साल वाले diabetic के लिए D — context से सब बदलता है।",
    "detail.personalized_label": "आपके लिए personalized · profile",
    "detail.profile_complete": "complete",
    "detail.methodology_q": "यह score कैसे calculate हुआ?",
    "detail.methodology_sub": "(48 fields → 6 criteria, weights के साथ)",

    "scorecard.coverage_blurb": "Claim time पर वास्तव में क्या cover है",
    "scorecard.cost_blurb": "अप्रत्याशित out-of-pocket खर्च की संभावना",
    "scorecard.waiting_blurb": "Policy कब से उपयोगी होगी",
    "scorecard.claim_blurb": "बीमाकर्ता claim time पर payment करेगा?",
    "scorecard.renewal_blurb": "70+ की उम्र में policy renew कर पाएंगे?",
    "scorecard.bonus_blurb": "Claim-free वर्षों के लिए पुरस्कार",
    "scorecard.weighted_avg": "6 criteria का weighted average। Rules-based — कोई LLM scoring loop में नहीं।",

    "footer.disclaimer": "केवल सलाह के लिए। Policy documents पर आधारित जानकारी; खरीदने से पहले बीमाकर्ता से verify करें। सभी ratings illustrative हैं।",

    "suggested.q1": "मुझे एक नई health insurance policy चाहिए।",
    "suggested.q2": "Pre-existing diseases की waiting period क्या है?",
    "suggested.q3": "क्या HDFC ERGO Optima Secure में AYUSH cover है?",
    "suggested.q4": "Care Supreme में room rent cap क्या है?",

    "grade.a": "मजबूत all-rounder — खरीदार के लिए ठोस विकल्प।",
    "grade.b": "अच्छी policy, कुछ notable gaps के साथ।",
    "grade.c": "ठीक-ठाक baseline; sign करने से पहले trade-offs जाँचें।",
    "grade.d": "गंभीर concerns — सिर्फ specific use-case के लिए ठीक।",
    "grade.f": "बड़े gaps — alternative options बेहतर होंगे।",

    "card.see_score_pill": "स्कोर देखें",
    "card.see_score_sub": "profile बनाएं",
    "card.score_locked_msg": "अपनी profile पूरी करें — मैं इस policy को आपके लिए score करूंगा।",
  },
} as const;

// Glossary — plain-English/Hindi explanations of insurance jargon.
// Used by the <Jargon> wrapper component to render an info-icon popover
// the user clicks for a 1-2 line explanation + everyday example.
export const GLOSSARY: Record<string, { en: { title: string; body: string }; hi: { title: string; body: string } }> = {
  PED: {
    en: { title: "Pre-Existing Disease (PED)", body: "A health condition you already have when you buy the policy — diabetes, BP, thyroid, anything chronic. Most policies don't cover it for the first 24-48 months. Be honest about yours: hiding it gets your claim denied later." },
    hi: { title: "Pre-Existing Disease (पहले से चली आ रही बीमारी)", body: "जो बीमारी आपको policy खरीदते समय पहले से है — diabetes, BP, थायरॉइड etc. ज़्यादातर policies शुरू के 24-48 महीनों में cover नहीं करतीं। ईमानदारी से बताइए, छिपाने से claim बाद में reject हो जाता है।" },
  },
  AYUSH: {
    en: { title: "AYUSH coverage", body: "Whether the policy pays for Ayurveda, Yoga, Unani, Siddha, and Homeopathy treatments at recognised hospitals. If you use these traditional systems, this matters; if you only use allopathic care, less so." },
    hi: { title: "AYUSH कवर", body: "क्या policy आयुर्वेद, योग, यूनानी, सिद्ध, और होम्योपैथी treatments को cover करती है। अगर आप इन पारंपरिक चिकित्सा का उपयोग करते हैं, यह ज़रूरी है।" },
  },
  NCB: {
    en: { title: "No-Claim Bonus (NCB)", body: "Reward for not claiming in a year — your sum insured goes up (typically 25-50%) without raising your premium. Bigger NCB compounds over years if you stay claim-free." },
    hi: { title: "No-Claim Bonus (NCB)", body: "बिना claim किए साल पूरा करने का इनाम — sum insured बढ़ जाता है (आम तौर पर 25-50%) बिना premium बढ़ाए।" },
  },
  SI: {
    en: { title: "Sum Insured (SI)", body: "The maximum amount the insurer pays in a policy year. For a single hospitalisation in a metro, ₹10L is the floor; ₹20L+ is safer if you have parents or family to cover." },
    hi: { title: "Sum Insured (बीमित राशि)", body: "एक policy साल में बीमाकर्ता अधिकतम कितना देगा। Metro में एक hospitalisation के लिए ₹10L न्यूनतम; ₹20L+ माता-पिता या परिवार के लिए सुरक्षित।" },
  },
  CSR: {
    en: { title: "Claim Settlement Ratio (CSR)", body: "Of every 100 claims the insurer received, how many they paid. IRDAI publishes this annually. <90% = caution; 95%+ = excellent. Single most predictive metric of 'will my claim get paid'." },
    hi: { title: "Claim Settlement Ratio", body: "100 claims में से बीमाकर्ता कितने pay करता है। IRDAI सालाना publish करता है। <90% = सावधान; 95%+ = बढ़िया।" },
  },
  Cashless: {
    en: { title: "Cashless treatment", body: "You don't pay the hospital — the insurer pays them directly via a pre-authorisation. Only works at network hospitals. Without it, you pay upfront and file for reimbursement later." },
    hi: { title: "Cashless इलाज", body: "आप hospital को सीधे payment नहीं करते — बीमाकर्ता pre-authorisation से payment करता है। सिर्फ network hospitals पर काम करता है।" },
  },
  TAT: {
    en: { title: "Cashless TAT (Turnaround Time)", body: "How fast the insurer approves your cashless pre-auth at the hospital desk. ≤2 hours = gold standard; ≥24h = your family pays cash first and waits for reimbursement." },
    hi: { title: "Cashless TAT", body: "बीमाकर्ता hospital में cashless approval कितनी जल्दी देता है। ≤2 घंटे = बढ़िया; ≥24 घंटे = परिवार को पहले cash देना पड़ेगा।" },
  },
  UIN: {
    en: { title: "Unique Identification Number (UIN)", body: "IRDAI-assigned ID for each policy product — proves it's a regulator-approved plan. You can search a UIN on irdai.gov.in to verify the policy exists and see its filed terms." },
    hi: { title: "UIN (Unique ID)", body: "IRDAI द्वारा हर policy को दिया गया ID — यह साबित करता है कि policy regulator से approved है।" },
  },
  CoPay: {
    en: { title: "Co-payment", body: "The % of every claim YOU pay out of pocket. 20% co-pay on a ₹5L hospital bill = you pay ₹1L; insurer pays ₹4L. Lower premium upfront, but bigger surprise at claim time." },
    hi: { title: "Co-payment", body: "हर claim का जो % आप अपनी जेब से देते हैं। ₹5L hospital bill पर 20% co-pay = आप ₹1L दें, बीमाकर्ता ₹4L।" },
  },
  Deductible: {
    en: { title: "Deductible", body: "Fixed rupee amount you pay BEFORE the insurer starts paying. ₹50k deductible = first ₹50k of every claim is on you. Reduces premium significantly but adds out-of-pocket risk." },
    hi: { title: "Deductible", body: "वो fixed amount जो आप बीमाकर्ता के payment शुरू करने से पहले देते हैं।" },
  },
  Floater: {
    en: { title: "Family Floater", body: "One sum insured shared by everyone in the family. ₹15L floater for 4 people = anyone (or everyone) can use up to ₹15L combined. Cheaper than individual policies if claims are rare." },
    hi: { title: "Family Floater", body: "एक sum insured पूरे परिवार के लिए share होती है। 4 लोगों के लिए ₹15L floater = कोई भी ₹15L तक use कर सकता है।" },
  },
  SubLimit: {
    en: { title: "Sub-limit", body: "A cap WITHIN your sum insured for a specific treatment — e.g., room rent capped at 1% of SI, or maternity capped at ₹50k. Watch for these — they're the #1 reason actual reimbursement < bill." },
    hi: { title: "Sub-limit", body: "Sum insured के अंदर कुछ खास treatments पर एक सीमा — जैसे room rent SI का 1%, या maternity ₹50k तक। यह सबसे बड़ी वजह है कि real payment bill से कम होता है।" },
  },
  RoomRent: {
    en: { title: "Room rent capping", body: "Some policies pay only up to a % of SI per day of hospital room — e.g., 1% of ₹5L = ₹5k/day. Choose a more expensive room and ALL your other charges get scaled down proportionally. Look for 'No room rent limit'." },
    hi: { title: "Room rent capping", body: "कई policies hospital room के लिए सिर्फ SI का % देती हैं — जैसे 1% का ₹5L = ₹5k/दिन। महंगा कमरा लें तो सभी अन्य charges भी scale down हो जाते हैं।" },
  },
};


export type StringKey = keyof typeof UI_STRINGS["en"];

export function translate(lang: UILang, key: StringKey, vars?: Record<string, string | number>): string {
  const dict = UI_STRINGS[lang] || UI_STRINGS.en;
  let s: string = (dict as Record<string, string>)[key] || (UI_STRINGS.en as Record<string, string>)[key] || key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.split(`\${${k}}`).join(String(v));
    }
  }
  return s;
}
