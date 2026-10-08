"""External evidence shown in the treatment table of the screen.

Why external evidence instead of model estimates?
    The data cannot identify what would happen if a treatment were started now rather
    than in an hour (see docs/design_rationale.md). Each treatment row therefore shows
    what randomised trials (and, clearly labelled, observational studies) found, on the
    trials' own outcomes and time frames. Every number below was checked against the
    source cited next to it (docs/sources.md lists how).

Each citation records its design ("trial" or "observational") so the screen can say which kind
of evidence a row rests on. An entry with ``"role": "caution"`` warns about the evidence rather
than adding to it.

Status values
    estimate           this project estimated the contrast with a validated method (none yet)
    external_only      only external evidence is shown
    not_estimable      neither this project nor trials can answer it; the reason is shown
"""

from __future__ import annotations

CITATIONS = {
    "censer": {
        "label": "CENSER, Permpikul 2019",
        "url": "https://doi.org/10.1164/rccm.201806-1034OC",
        "design": "trial",
    },
    "clovers": {
        "label": "CLOVERS, NEJM 2023",
        "url": "https://doi.org/10.1056/NEJMoa2212663",
        "design": "trial",
    },
    "classic": {
        "label": "CLASSIC, Meyhoff 2022",
        "url": "https://doi.org/10.1056/NEJMoa2202707",
        "design": "trial",
    },
    "sixty_five": {
        "label": "65 trial, Lamontagne 2020",
        "url": "https://doi.org/10.1001/jama.2020.0930",
        "design": "trial",
    },
    "phantasi": {
        "label": "PHANTASi, Alam 2018",
        "url": "https://doi.org/10.1016/S2213-2600(17)30469-1",
        "design": "trial",
    },
    "pak": {
        "label": "Pak 2023 (observational)",
        "url": "https://doi.org/10.1093/cid/ciad450",
        "design": "observational",
    },
    "li": {
        "label": "Li 2026 (observational, includes MIMIC-IV)",
        "url": "https://doi.org/10.2147/clep.s588212",
        "design": "observational",
    },
    "dai": {
        "label": "Dai 2026 (observational, MIMIC-IV)",
        "url": "https://doi.org/10.1016/j.isci.2026.116584",
        "design": "observational",
    },
}

EVIDENCE_TABLE = {
    "note": (
        "Trial results describe their own outcomes and follow-up (death at 28 or 90 days, shock control "
        "at 6 hours). They are not the 24-hour progression risk shown above and cannot be subtracted from it."
    ),
    "citations": CITATIONS,
    "rows": [
        {
            "treatment": "Norepinephrine",
            "show_when": "not_on_vasopressor",
            "status": "external_only",
            "effect": [
                {
                    "text": (
                        "Early low-dose norepinephrine in emergency-department patients with sepsis-related "
                        "hypotension raised shock control at 6 h (76.1% vs 48.4%). 28-day death 15.5% vs 21.9%, "
                        "not statistically different (P = 0.15). Single centre, phase II, n = 310."
                    ),
                    "citation": "censer",
                },
                {
                    "text": (
                        "A strategy that prioritised vasopressors and less fluid after the first 1–3 L did not "
                        "change death before discharge home by day 90 (14.0% vs 14.9%; difference −0.9 points, "
                        "95% CI −4.4 to 2.6)."
                    ),
                    "citation": "clovers",
                },
                {
                    "text": (
                        "Patients aged 65 or older: aiming for a lower blood pressure (MAP 60–65) to reduce "
                        "vasopressor exposure gave 90-day death 41.0% vs 43.8% (95% CI for the difference "
                        "−6.75 to 1.05 points)."
                    ),
                    "citation": "sixty_five",
                    "only_if_age_at_least": 65,
                },
            ],
            "timing": [
                {
                    "text": "CENSER compared norepinephrine started at a median 93 vs 192 minutes.",
                    "citation": "censer",
                },
                {
                    "text": (
                        "Observational: starting vasopressors 0–1 h vs 1–3 h after haemodynamic instability, "
                        "28-day death hazard ratio 1.07 (0.89–1.29)."
                    ),
                    "citation": "li",
                },
            ],
        },
        {
            "treatment": "IV fluids",
            "show_when": "always",
            "status": "external_only",
            "effect": [
                {
                    "text": (
                        "After the first 1–3 L, restrictive and liberal fluid strategies did not differ in "
                        "death before discharge home by day 90 (−0.9 points, 95% CI −4.4 to 2.6)."
                    ),
                    "citation": "clovers",
                },
                {
                    "text": (
                        "In ICU septic shock after at least 1 L, restricting fluid did not change 90-day death "
                        "(42.3% vs 42.1%; adjusted difference 0.1 points, 95% CI −4.7 to 4.9)."
                    ),
                    "citation": "classic",
                },
                {
                    "text": "Neither trial tested the timing of a bolus or the first 30 mL/kg.",
                    "citation": None,
                },
            ],
            "timing": [
                {
                    "text": (
                        "No randomised trial of timing. Observational: starting within 1 h with at least "
                        "30 mL/kg by 3 h vs starting at 1–3 h, 28-day death hazard ratio 0.72 (0.53–0.97)."
                    ),
                    "citation": "li",
                },
                {
                    "role": "caution",
                    "text": (
                        "Caution: in MIMIC-IV, two reasonable fluid exposure definitions gave opposite "
                        "conclusions in another study (balanced crystalloid vs saline, major adverse kidney "
                        "events at 30 days: odds ratio 0.49 under one definition, 2.51 under the other)."
                    ),
                    "citation": "dai",
                },
            ],
        },
        {
            "treatment": "Antibiotics",
            "show_when": "before_first_antibiotic",
            "status": "external_only",
            "already_started_text": (
                "Antibiotics were started at or before sepsis recognition (every patient in this cohort has "
                "started them). Timing evidence applies only before the first dose."
            ),
            "effect": [
                {
                    "text": "No trial compares starting with not starting antibiotics in sepsis.",
                    "citation": None,
                }
            ],
            "timing": [
                {
                    "text": (
                        "Antibiotics given before hospital arrival (a median 26 minutes earlier) did not change "
                        "28-day death (8% vs 8%; n = 2,672)."
                    ),
                    "citation": "phantasi",
                },
                {
                    "text": (
                        "Observational, patients treated within 6 h: each hour of delay was associated with "
                        "higher death in septic shock (odds ratio 1.07, 1.04–1.11) but not in sepsis without "
                        "shock (1.03, 0.98–1.09)."
                    ),
                    "citation": "pak",
                },
            ],
        },
        *[
            {
                "treatment": combination,
                "show_when": "always",
                "status": "not_estimable",
                "reason": (
                    "No trial compares this combination at these times. In these data, combinations are given "
                    "because of how ill the patient looks, and some are almost always given together, so a "
                    "difference could not be separated from that."
                ),
            }
            for combination in (
                "IV fluids + norepinephrine",
                "Antibiotics + IV fluids",
                "Antibiotics + norepinephrine",
                "Antibiotics + IV fluids + norepinephrine",
            )
        ],
    ],
}
