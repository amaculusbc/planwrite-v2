"""Compliance validation service.

Checks content for banned phrases, responsible gaming requirements,
SEO best practices, and link validation.
"""

import re
from typing import Any
from dataclasses import dataclass, field
from enum import Enum

from app.services.operator_profile import (
    CONTENT_MODE_DFS,
    CONTENT_MODE_PREDICTION_MARKET,
    get_content_mode_offer,
    normalize_operator,
)

class IssueSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ComplianceIssue:
    """A single compliance issue found in content."""

    type: str
    message: str
    severity: IssueSeverity = IssueSeverity.WARNING
    location: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "message": self.message,
            "severity": self.severity.value,
            "location": self.location,
            "suggestion": self.suggestion,
        }


@dataclass
class ComplianceResult:
    """Result of compliance validation."""

    valid: bool
    issues: list[ComplianceIssue] = field(default_factory=list)
    word_count: int = 0
    compliance_score: float = 100.0

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "issues": [i.to_dict() for i in self.issues],
            "word_count": self.word_count,
            "compliance_score": self.compliance_score,
        }


# Banned phrases that indicate non-compliant content
BANNED_PATTERNS = [
    (r"\bsurefire\b", "Avoid 'surefire' - implies guaranteed outcomes"),
    (r"\bguarantee[d]?\b", "Avoid 'guarantee' - no betting outcomes are guaranteed"),
    (r"\brisk[-\s]?free\b(?! bet credit)", "Avoid 'risk-free' unless referring to bet credits"),
    (r"\bcan'?t lose\b", "Avoid 'can't lose' - misleading claim"),
    (r"\bfree money\b", "Avoid 'free money' - misleading"),
    (r"\beasy win\b", "Avoid 'easy win' - misleading claim"),
    (r"\bno[- ]brainer\b", "Avoid 'no-brainer' - implies certainty"),
]

# Triggers that require responsible gaming language
BET_TRIGGERS = [
    r"\bbet\b",
    r"\bwager\b",
    r"\bparlay\b",
    r"\bgambl",
    r"\bsportsbook\b",
]

# State-specific disclaimers
STATE_DISCLAIMERS = {
    "ALL": "21+. Gambling problem? Call 1-800-GAMBLER. Please bet responsibly.",
    "NY": "21+. Gambling problem? Call 877-8-HOPENY or text HOPENY (467369).",
    "AZ": "21+. Gambling problem? Call 1-800-NEXT-STEP.",
    "PA": "21+. Gambling problem? Call 1-800-GAMBLER.",
    "NJ": "21+. Gambling problem? Call 1-800-GAMBLER.",
    "CO": "21+. Gambling problem? Call 1-800-522-4700.",
    "MI": "21+. Gambling problem? Call 1-800-270-7117.",
    "VA": "21+. Gambling problem? Call 1-888-532-3500.",
    "OH": "21+. If you or a loved one has a gambling problem, call 1-800-589-9966.",
    "MA": "21+. Gambling problem? Call 1-800-327-5050.",
    "KY": "21+. Gambling problem? Call 1-800-522-4700.",
}

# Allowed domains for external links
ALLOWED_DOMAINS = [
    "example.com",  # Replace with your actual domains
]


def _extract_expiration_days(terms: str | None) -> int | None:
    """Extract expiration days from terms text without defaulting."""
    if not terms:
        return None
    text = terms.lower()
    patterns = [
        r"expire[sd]?\s+(?:in|within)\s+(\d+)\s+days?",
        r"valid\s+for\s+(\d+)\s+days?",
        r"must\s+be\s+used\s+within\s+(\d+)\s+days?",
        r"(\d+)[-\s]day\s+expiration",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def check_offer_facts(
    content: str,
    offer: dict[str, Any] | None = None,
    keyword: str | None = None,
) -> list[ComplianceIssue]:
    """Check offer-specific facts (bonus code, expiration days, keyword density)."""
    issues: list[ComplianceIssue] = []
    if not content:
        return issues

    if offer:
        bonus_code = str(offer.get("bonus_code") or "").strip()
        if bonus_code and not re.search(re.escape(bonus_code), content, re.IGNORECASE):
            issues.append(ComplianceIssue(
                type="missing_bonus_code",
                message=f"Bonus code '{bonus_code}' not found in content",
                severity=IssueSeverity.ERROR,
                suggestion="Include the exact promo code in the article body",
            ))

        expected_days = offer.get("bonus_expiration_days")
        if expected_days is None:
            expected_days = _extract_expiration_days(str(offer.get("terms") or ""))

        if expected_days is not None:
            matches = re.findall(
                r"expire[sd]?\s+(?:in|within)\s+(\d+)\s+days?",
                content,
                flags=re.IGNORECASE,
            )
            for match in matches:
                try:
                    found_days = int(match)
                except ValueError:
                    continue
                if found_days != int(expected_days):
                    issues.append(ComplianceIssue(
                        type="expiration_mismatch",
                        message=f"Expiration mismatch: content says {found_days} days, expected {expected_days} days",
                        severity=IssueSeverity.ERROR,
                        suggestion="Update expiration days to match the offer terms",
                    ))
                    break

    if keyword:
        keyword_count = len(re.findall(re.escape(keyword), content, flags=re.IGNORECASE))
        if keyword_count < 5:
            issues.append(ComplianceIssue(
                type="keyword_density_low",
                message=f"Low keyword density: '{keyword}' appears {keyword_count} times (target 5-9)",
                severity=IssueSeverity.WARNING,
                suggestion="Include the exact keyword a few more times naturally",
            ))
        elif keyword_count > 9:
            issues.append(ComplianceIssue(
                type="keyword_density_high",
                message=f"High keyword density: '{keyword}' appears {keyword_count} times (target 5-9)",
                severity=IssueSeverity.WARNING,
                suggestion="Reduce exact keyword repetitions to avoid stuffing",
            ))

    return issues


def get_disclaimer_for_state(state: str) -> str:
    """Get the appropriate disclaimer for a state."""
    return STATE_DISCLAIMERS.get(state.upper(), STATE_DISCLAIMERS["ALL"])


def check_banned_phrases(content: str) -> list[ComplianceIssue]:
    """Check for banned/non-compliant phrases."""
    issues = []

    for pattern, message in BANNED_PATTERNS:
        matches = list(re.finditer(pattern, content, flags=re.IGNORECASE))
        for match in matches:
            issues.append(ComplianceIssue(
                type="banned_phrase",
                message=message,
                severity=IssueSeverity.ERROR,
                location=f"'{match.group()}' at position {match.start()}",
                suggestion="Remove or rephrase this term",
            ))

    return issues


def check_responsible_gaming(content: str) -> list[ComplianceIssue]:
    """Check that betting content includes responsible gaming language."""
    issues = []

    has_bet_trigger = any(
        re.search(pattern, content, flags=re.IGNORECASE)
        for pattern in BET_TRIGGERS
    )

    if has_bet_trigger:
        has_responsible = any(phrase in content.lower() for phrase in [
            "responsible",
            "21+",
            "gambler",
            "gambling problem",
            "bet responsibly",
        ])

        if not has_responsible:
            issues.append(ComplianceIssue(
                type="responsible_gaming",
                message="Content mentions betting but lacks responsible gaming language",
                severity=IssueSeverity.ERROR,
                suggestion="Add '21+' and responsible gaming disclaimer",
            ))

    return issues


def check_cta_links(content: str) -> list[ComplianceIssue]:
    """Verify CTA links are present and properly formatted."""
    issues = []

    cta_pattern = re.compile(r"\[Claim Offer\]\(([^)]+)\)", re.IGNORECASE)
    html_cta_pattern = re.compile(r"<a\s+[^>]*>.*?Claim Offer.*?</a>", re.IGNORECASE | re.DOTALL)
    switchboard_anchor_pattern = re.compile(
        r'<a\b[^>]*href\s*=\s*(["\'])https?://[^"\']*switchboard\.[^"\']+/offers[^"\']*\1[^>]*>',
        re.IGNORECASE,
    )
    switchboard_tracking_pattern = re.compile(r'data-id\s*=\s*(["\'])switchboard_tracking\1', re.IGNORECASE)
    bam_shortcode_pattern = re.compile(r"\[bam-inline-promotion\b", re.IGNORECASE)

    has_cta = any([
        bool(cta_pattern.search(content or "")),
        bool(html_cta_pattern.search(content or "")),
        bool(switchboard_anchor_pattern.search(content or "")),
        bool(switchboard_tracking_pattern.search(content or "")),
        bool(bam_shortcode_pattern.search(content or "")),
    ])

    if not has_cta:
        issues.append(ComplianceIssue(
            type="missing_cta",
            message="No CTA link found",
            severity=IssueSeverity.WARNING,
            suggestion="Add a promo module, switchboard CTA, or explicit 'Claim Offer' link",
        ))

    return issues


def check_link_quality(content: str, allowed_domains: list[str] | None = None) -> list[ComplianceIssue]:
    """Check link quality and anchor text."""
    issues = []
    domains = allowed_domains or ALLOWED_DOMAINS

    # Find all markdown links
    link_pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")

    for match in link_pattern.finditer(content):
        anchor = match.group(1).strip()
        url = match.group(2)

        # Check anchor length
        if len(anchor.split()) < 2:
            issues.append(ComplianceIssue(
                type="short_anchor",
                message=f"Anchor text too short: '{anchor}'",
                severity=IssueSeverity.WARNING,
                location=url,
                suggestion="Use descriptive anchor text (2+ words)",
            ))

        # Check for external domains (if domain list provided)
        if domains and not any(domain in url for domain in domains):
            issues.append(ComplianceIssue(
                type="external_link",
                message=f"External link detected: {url}",
                severity=IssueSeverity.INFO,
                suggestion="Verify external link is appropriate",
            ))

    for url, _, anchor_text in _extract_html_links(content):
        anchor = anchor_text.strip()
        if len(anchor.split()) < 2 and "switchboard." not in url.lower():
            issues.append(ComplianceIssue(
                type="short_anchor",
                message=f"Anchor text too short: '{anchor}'",
                severity=IssueSeverity.WARNING,
                location=url,
                suggestion="Use descriptive anchor text (2+ words)",
            ))
        if domains and "switchboard." not in url.lower() and not any(domain in url for domain in domains):
            issues.append(ComplianceIssue(
                type="external_link",
                message=f"External link detected: {url}",
                severity=IssueSeverity.INFO,
                suggestion="Verify external link is appropriate",
            ))

    # Check for links in headings
    heading_link_pattern = re.compile(r"^#+ .*\]\(", re.MULTILINE)
    html_heading_link_pattern = re.compile(r"<h[1-6][^>]*>.*?<a\b[^>]*>.*?</a>.*?</h[1-6]>", re.IGNORECASE | re.DOTALL)
    if heading_link_pattern.search(content) or html_heading_link_pattern.search(content):
        issues.append(ComplianceIssue(
            type="heading_link",
            message="Link found in heading",
            severity=IssueSeverity.WARNING,
            suggestion="Move links from headings to body text",
        ))

    return issues


def _strip_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "")


def _first_paragraph_text(content: str) -> str:
    match = re.search(r"<p>(.*?)</p>", content or "", flags=re.IGNORECASE | re.DOTALL)
    if match:
        return _strip_html_tags(match.group(1))
    return (content or "").split("\n\n", 1)[0]


def _extract_html_links(content: str) -> list[tuple[str, str, str]]:
    """Return list of (url, anchor_html, anchor_text) for HTML anchors."""
    pattern = re.compile(
        r"<a\b([^>]*)href\s*=\s*(['\"])(?P<url>https?://[^'\"]+)\2[^>]*>(?P<inner>.*?)</a>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    links: list[tuple[str, str, str]] = []
    for match in pattern.finditer(content or ""):
        url = match.group("url")
        inner = match.group("inner") or ""
        anchor_text = _strip_html_tags(inner)
        links.append((url, inner, anchor_text))
    return links


def check_editorial_regressions(
    content: str,
    *,
    keyword: str | None = None,
    offer: dict[str, Any] | None = None,
) -> list[ComplianceIssue]:
    """Catch high-value editorial regressions seen in V2 feedback."""
    issues: list[ComplianceIssue] = []
    if not content:
        return issues

    offer = offer or {}
    brand = str(offer.get("brand") or "").strip()
    brand_operator = normalize_operator(brand)

    # Main keyword should appear in first paragraph (ideally sentence 1-2).
    if keyword:
        first_para = _first_paragraph_text(content)
        if not re.search(re.escape(keyword), first_para, flags=re.IGNORECASE):
            issues.append(ComplianceIssue(
                type="main_keyword_missing_early",
                message=f"Primary keyword '{keyword}' not found in the first paragraph",
                severity=IssueSeverity.WARNING,
                suggestion="Include the exact keyword in sentence 1 or 2 of the intro",
            ))

    links = _extract_html_links(content)

    # Excessive in-body switchboard links create CTA overuse and poor UX.
    switchboard_links = [
        (url, inner, text)
        for url, inner, text in links
        if "switchboard." in url.lower() and "/offers" in url.lower()
    ]
    if len(switchboard_links) > 1:
        issues.append(ComplianceIssue(
            type="switchboard_link_overuse",
            message=f"Too many switchboard links: {len(switchboard_links)} found (target <= 1 in body)",
            severity=IssueSeverity.WARNING,
            suggestion="Keep switchboard links to primary CTA placements and use internal links elsewhere",
        ))

    non_switchboard_links = [
        (url, inner, text)
        for url, inner, text in links
        if not ("switchboard." in url.lower() and "/offers" in url.lower())
    ]
    if len(non_switchboard_links) > 1:
        issues.append(ComplianceIssue(
            type="internal_link_overuse",
            message=f"Too many internal/external links: {len(non_switchboard_links)} found (target <= 1 in body)",
            severity=IssueSeverity.WARNING,
            suggestion="Keep only the most relevant supporting link in the body copy",
        ))

    # Duplicate internal links by URL (excluding switchboard).
    duplicate_urls: set[str] = set()
    seen_internal: set[str] = set()
    for url, _, _ in links:
        url_lc = url.lower()
        if "switchboard." in url_lc and "/offers" in url_lc:
            continue
        if url_lc in seen_internal:
            duplicate_urls.add(url)
        else:
            seen_internal.add(url_lc)
    for dup in sorted(duplicate_urls):
        issues.append(ComplianceIssue(
            type="duplicate_internal_link",
            message=f"Internal link URL repeated: {dup}",
            severity=IssueSeverity.WARNING,
            suggestion="Use each internal link URL once per article unless repetition is necessary",
        ))

    # CTA/brand mismatch: switchboard anchor text should not reference a competitor brand.
    if brand_operator:
        for url, _, anchor_text in switchboard_links:
            anchor_operator = normalize_operator(anchor_text)
            if anchor_operator and anchor_operator != brand_operator:
                issues.append(ComplianceIssue(
                    type="cta_brand_mismatch",
                    message=f"Switchboard CTA anchor references '{anchor_operator}' on a '{brand_operator}' article",
                    severity=IssueSeverity.ERROR,
                    location=url,
                    suggestion="Use the correct operator brand in the CTA anchor text",
                ))
                break

    # Mode-language mismatch (Novig/Kalshi/Polymarket and DFS apps).
    mode = get_content_mode_offer(offer, keyword=keyword or "")
    plain = _strip_html_tags(content)
    plain = re.sub(r"https?://\S+", " ", plain)
    if mode in {CONTENT_MODE_PREDICTION_MARKET, CONTENT_MODE_DFS}:
        mismatches = []
        if re.search(r"\bbonus bets?\b", plain, flags=re.IGNORECASE):
            mismatches.append("bonus bets")
        if re.search(r"\bsportsbooks?\b", plain, flags=re.IGNORECASE):
            mismatches.append("sportsbook")
        if re.search(r"\bwager(?:ing)?\b", plain, flags=re.IGNORECASE):
            mismatches.append("wager/wagering")
        if re.search(r"\bbet(?:ting)?\b", plain, flags=re.IGNORECASE):
            mismatches.append("bet/betting")
        if mismatches:
            label = "prediction-market" if mode == CONTENT_MODE_PREDICTION_MARKET else "DFS"
            issues.append(ComplianceIssue(
                type="mode_language_mismatch",
                message=f"{label.title()} article contains sportsbook language ({', '.join(sorted(set(mismatches)))})",
                severity=IssueSeverity.WARNING,
                suggestion="Use operator-appropriate language (prediction market or DFS terms) throughout the article",
            ))

    if brand_operator == "bet365" and re.search(r"\bbet365 promo code\b", plain, flags=re.IGNORECASE):
        issues.append(ComplianceIssue(
            type="bet365_keyword_mismatch",
            message="bet365 article uses 'promo code' instead of house-style 'bonus code'",
            severity=IssueSeverity.WARNING,
            suggestion="Use 'bet365 bonus code' in visible copy",
        ))

    return issues


def check_seo(content: str) -> list[ComplianceIssue]:
    """Check SEO best practices."""
    issues = []

    # Check paragraph length
    html_paragraphs = [
        _strip_html_tags(p).strip()
        for p in re.findall(r"<p\b[^>]*>(.*?)</p>", content or "", flags=re.IGNORECASE | re.DOTALL)
    ]
    paragraphs = [p for p in html_paragraphs if p] or [
        p for p in content.split("\n\n") if p.strip() and not p.strip().startswith("#")
    ]
    long_paragraphs = [p for p in paragraphs if len(p.split()) > 140]

    if long_paragraphs:
        issues.append(ComplianceIssue(
            type="long_paragraph",
            message=f"{len(long_paragraphs)} paragraph(s) exceed ~140 words",
            severity=IssueSeverity.WARNING,
            suggestion="Break long paragraphs into smaller chunks",
        ))

    # Check link density
    markdown_link_count = len(re.findall(r"\]\((https?://[^)]+)\)", content))
    html_link_count = len(_extract_html_links(content))
    link_count = markdown_link_count + html_link_count
    word_count = len(_strip_html_tags(content).split())

    if word_count > 0 and link_count / word_count > (1 / 120):
        issues.append(ComplianceIssue(
            type="link_density",
            message="Link density too high (> 1 per ~120 words)",
            severity=IssueSeverity.WARNING,
            suggestion="Reduce number of links or add more content",
        ))

    # Check heading hierarchy
    headings = re.findall(r"^(#{1,6}) ", content, re.MULTILINE)
    if not headings:
        headings = [("#" * int(level)) for level in re.findall(r"<h([1-6])\b", content, re.IGNORECASE)]
    if headings:
        levels = [len(h) for h in headings]
        for i in range(1, len(levels)):
            if levels[i] > levels[i - 1] + 1:
                issues.append(ComplianceIssue(
                    type="heading_skip",
                    message="Heading level skipped (e.g., H2 to H4)",
                    severity=IssueSeverity.INFO,
                    suggestion="Use sequential heading levels (H1 → H2 → H3)",
                ))
                break

    return issues


def validate_content(
    content: str,
    state: str = "ALL",
    check_links: bool = True,
    allowed_domains: list[str] | None = None,
    keyword: str | None = None,
    offer: dict[str, Any] | None = None,
) -> ComplianceResult:
    """Run all compliance checks on content.

    Returns a ComplianceResult with all issues found.
    """
    issues: list[ComplianceIssue] = []

    # Run all checks
    issues.extend(check_banned_phrases(content))
    issues.extend(check_responsible_gaming(content))
    issues.extend(check_cta_links(content))
    issues.extend(check_seo(content))
    issues.extend(check_offer_facts(content, offer=offer, keyword=keyword))
    issues.extend(check_editorial_regressions(content, keyword=keyword, offer=offer))

    if check_links:
        issues.extend(check_link_quality(content, allowed_domains))

    # Calculate metrics
    word_count = len(_strip_html_tags(content).split())
    error_count = sum(1 for i in issues if i.severity == IssueSeverity.ERROR)
    warning_count = sum(1 for i in issues if i.severity == IssueSeverity.WARNING)

    # Score: start at 100, deduct for issues
    score = 100.0
    score -= error_count * 15
    score -= warning_count * 5
    score = max(0.0, score)

    return ComplianceResult(
        valid=error_count == 0,
        issues=issues,
        word_count=word_count,
        compliance_score=score,
    )
