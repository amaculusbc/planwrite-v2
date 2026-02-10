"""Content guidelines for sports betting promo articles.

Defines tone, compliance rules, and style constraints.
"""

GUIDELINES = {
    "core_principles": {
        "compliance_first": {
            "description": "Maintain strict compliance with US gambling regulations. Never imply guaranteed outcomes.",
            "prohibited_claims": [
                "risk-free",
                "guaranteed win",
                "easy money",
                "sure thing",
                "can't lose",
                "surefire",
            ],
        },
        "authentic_tone": {
            "description": "Write like an informed person having a genuine conversation - not a promotional advertisement.",
            "avoid_phrases": [
                "FABULOUS APP! FUN AND ENGAGING!",
                "Experience the thrill like never before!",
                "Revolutionary gaming experience",
                "Premier online sports betting platform",
                "With generous bonuses, a user-friendly app, and a commitment to responsible gambling",
            ],
            "overused_words": [
                "premier",
                "generous",
                "solid choice",
                "stands out",
                "commitment to",
                "user-friendly",
                "exciting",
                "amazing",
                "incredible",
                "outstanding",
                "exceptional",
                "revolutionary",
            ],
        },
    },
    "tone": {
        "voice": "conversational",
        "perspective": "second_person",
        "formality": "casual_informative",
        "max_sentence_length": 25,  # words
        "use_contractions": True,
        "avoid_jargon": True,
    },
    "content_structure": {
        "paragraph_length": "2-4 sentences, 40-70 words",
        "sentence_variety": "Mix short punchy sentences (8-12 words) with medium compound sentences (15-25 words)",
        "pacing": "Front-load key info (offer amount, code, eligibility), details later",
        "list_usage": "Minimal - prefer natural paragraph flow",
    },
    "compliance_requirements": {
        "state_specificity": "Mention state restrictions when applicable",
        "terms_transparency": "Link to or mention full terms",
    },
}


def get_style_instructions() -> str:
    """Return consolidated style instructions for prompts."""
    return """STYLE GUIDE (Top Stories - Sports Betting Promo):

VOICE & TONE:
- Conversational and informative, like a knowledgeable friend sharing a deal
- Use active voice, avoid passive constructions
- Use contractions naturally (do not sound robotic)
- NO EXCLAMATION POINTS anywhere in the content
- Excited but not overselling - avoid hyperbolic marketing language
- Honest about limitations, clear about requirements

FORBIDDEN PHRASES (never use):
- "risk-free" (except in official bonus name like "risk-free bet credit")
- "guaranteed win", "can't lose", "sure thing", "easy money"
- "revolutionary", "premier", "exceptional", "stands out as"
- "generous bonuses and user-friendly app" (overused cliché)
- Marketing hype like "experience the thrill like never before"
- Any exclamation points

SECTION VARIETY (critical - avoid repetition):
- Each section should ADD new information, not restate previous sections
- If the intro mentioned the states, don't list them again in every section
- If you explained the mechanic in Overview, don't re-explain it in Eligibility
- Later sections should be SHORTER and more specific
- Use varied sentence structures - not every section starts with "To..."
- Do NOT repeat responsible gaming disclaimers in multiple sections

SECTION-SPECIFIC GUIDANCE:
- Overview: Why this offer matters, what makes it valuable
- How to Claim: Worked example with dollar amounts and outcomes
- Eligibility: Who qualifies (brief) - skip restating the offer
- Terms: Fine print only - odds requirements, expirations, restrictions
- Responsible Gaming: 2-3 sentences max with helpline

SENTENCE STRUCTURE:
- Mix short (8-12 words) and medium (15-25 words) sentences
- Max 25 words per sentence
- Vary rhythm - don't start every sentence the same way

SIMPLER PHRASING (important):
- Prefer direct, plain sentences
- Good: "Bonus bets expire in seven days."
- Bad: "Timing is also crucial, as these bonus bets expire in 7 days, encouraging you to engage quickly."
- Avoid filler like "it’s important to note" or "in order to"

PARAGRAPH FLOW:
- 2-4 sentences per paragraph, 40-70 words total
- Front-load important info (offer amount, promo code, key dates)
- Details and fine print come later
- Natural flow over rigid list formatting

VOCABULARY:
- Beginner-friendly - explain betting terms inline if needed
- Say "bet" not "wager" (more natural)
- Avoid marketing jargon and clichés
- Be specific: "$150 in bonus bets" not "generous bonus"

COMPLIANCE (non-negotiable):
- Always mention 21+ age requirement
- Include responsible gaming helpline ONCE at the end of the article
- State-specific restrictions when applicable
- Never imply guaranteed outcomes
- No "risk-free" claims (unless quoting official bonus name)"""


def get_prohibited_patterns() -> list[str]:
    """Regex patterns for compliance checking."""
    return [
        r"\bguarantee(d)?\b(?! applies)",  # "guaranteed" unless "guarantee applies"
        r"\bsurefire\b",
        r"\bcan'?t lose\b",
        r"\beasy money\b",
        r"\brisk[-\s]?free\b(?! bet credit)",  # Allow "risk-free bet credit"
        r"\bpremier (?:online )?(?:sports )?betting platform\b",
        r"\bstands out as\b",
        r"\bcommitment to responsible gambling\b",  # Cliché phrasing
    ]


def get_temperature_by_section(section_type: str) -> float:
    """Return appropriate temperature for different content types."""
    temps = {
        "intro": 0.7,  # More natural variation
        "outline": 0.6,  # Creative for planning
        "h2": 0.5,  # Balanced creativity
        "h3": 0.4,  # More focused
        "terms": 0.3,  # Very precise for legal content
    }
    return temps.get(section_type, 0.5)


# Section objective templates for the Plan stage
SECTION_OBJECTIVES = {
    "overview": {
        "purpose": "Explain why this offer matters and what makes it valuable",
        "focus": "Value proposition, timing advantage, who it's for",
        "avoid": "Step-by-step instructions (save for How to Claim)",
        "length": "2-3 paragraphs",
    },
    "how_to_claim": {
        "purpose": "Provide a worked example with actual dollar amounts",
        "focus": "First-person example showing win/loss scenarios",
        "avoid": "Restating what the offer is (already covered)",
        "length": "2-3 paragraphs with calculations",
    },
    "key_details": {
        "purpose": "Cover essential eligibility and requirements",
        "focus": "21+, new users, eligible states, minimum odds, expiration",
        "avoid": "Repeating the full offer explanation",
        "length": "1-2 paragraphs",
    },
    "how_to_sign_up": {
        "purpose": "Step-by-step registration instructions",
        "focus": "Numbered list of 5 specific steps",
        "avoid": "Marketing language, repeating offer details",
        "length": "Numbered list only",
    },
    "terms": {
        "purpose": "Cover the fine print and legal requirements",
        "focus": "Wagering requirements, restrictions, disclaimers",
        "avoid": "Repeating eligibility (covered in Key Details)",
        "length": "1 paragraph + standard disclaimer",
    },
}


def get_section_objective(section_type: str) -> dict:
    """Get objective template for a section type."""
    return SECTION_OBJECTIVES.get(section_type, {
        "purpose": "Provide relevant information",
        "focus": "Stay on topic",
        "avoid": "Repetition from previous sections",
        "length": "2-3 paragraphs",
    })
