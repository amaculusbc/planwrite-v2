# Content Pipeline Issues Tracker

Updated: 2026-02-10

## Addressed In This Pass

1. `Critical` Legacy token flow was still used by generation API, which dropped section guidance.
Status: `Fixed`
Notes: `/api/generate/outline` and `/api/generate/draft` now use structured outline/draft flow, with support for `outline_text` and `outline_structured` plus token fallback.

2. `Critical` Terms sections could hallucinate legal content when offer terms were missing.
Status: `Fixed`
Notes: Terms sections are now deterministic and rendered from known terms/facts only. If terms are missing, output falls back to a safe "see full terms" statement.

3. `High` Expiration facts could be fabricated as `7 days` by default.
Status: `Fixed`
Notes: `extract_bonus_expiration_days` now returns `None` when no explicit value exists. Prompts now instruct writers to use "see full terms" instead of guessing.

4. `High` Multi-offer narrative used only the primary offer in section prompts.
Status: `Fixed`
Notes: Body/intro generation now receives all selected offers as source-of-truth context and includes multi-offer guidance to keep code-brand pairings correct.

## Open Backlog (Revisit)

1. Internal link guidance still includes placeholder links (`href="#"`) in fallback paths.
2. Competitor URL scraping still needs SSRF hardening and stricter URL validation.
3. Compliance link checks are markdown-focused and should be expanded for HTML output.
4. Automated tests are still thin for generation/compliance/offer parsing paths.
