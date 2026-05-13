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
  },
} as const;

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
