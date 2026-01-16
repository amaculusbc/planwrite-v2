"""Compliance validation service.

Checks content for banned phrases, responsible gaming requirements,
SEO best practices, and link validation.
"""

import re
from dataclasses import dataclass, field
from enum import Enum


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
    cta_matches = cta_pattern.findall(content)

    if len(cta_matches) < 1:
        issues.append(ComplianceIssue(
            type="missing_cta",
            message="No CTA link found",
            severity=IssueSeverity.WARNING,
            suggestion="Add at least one '[Claim Offer](url)' link",
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

    # Check for links in headings
    heading_link_pattern = re.compile(r"^#+ .*\]\(", re.MULTILINE)
    if heading_link_pattern.search(content):
        issues.append(ComplianceIssue(
            type="heading_link",
            message="Link found in heading",
            severity=IssueSeverity.WARNING,
            suggestion="Move links from headings to body text",
        ))

    return issues


def check_seo(content: str) -> list[ComplianceIssue]:
    """Check SEO best practices."""
    issues = []

    # Check paragraph length
    paragraphs = [p for p in content.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    long_paragraphs = [p for p in paragraphs if len(p.split()) > 130]

    if long_paragraphs:
        issues.append(ComplianceIssue(
            type="long_paragraph",
            message=f"{len(long_paragraphs)} paragraph(s) exceed ~120 words",
            severity=IssueSeverity.WARNING,
            suggestion="Break long paragraphs into smaller chunks",
        ))

    # Check link density
    link_count = len(re.findall(r"\]\((https?://[^)]+)\)", content))
    word_count = len(content.split())

    if word_count > 0 and link_count / word_count > (1 / 120):
        issues.append(ComplianceIssue(
            type="link_density",
            message="Link density too high (> 1 per ~120 words)",
            severity=IssueSeverity.WARNING,
            suggestion="Reduce number of links or add more content",
        ))

    # Check heading hierarchy
    headings = re.findall(r"^(#{1,6}) ", content, re.MULTILINE)
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

    if check_links:
        issues.extend(check_link_quality(content, allowed_domains))

    # Calculate metrics
    word_count = len(content.split())
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
