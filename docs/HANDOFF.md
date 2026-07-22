# PlanWrite v2 (TopStoriesGenerator) — Team Handoff

_Last updated: 2026-07-22. Written for the team taking over the project. Everything here is
verified against the code at commit `d7ecbb8` unless marked otherwise. Where a mechanism
exists because of a specific editorial incident, the incident is named — most of this
codebase only makes sense once you know what broke._

## What this is

An internal Better Collective tool that generates sports-betting promo articles ("top
stories") for BC properties — Action Network (sportsbook, prediction market, DFS offers)
and GOAL.com (its own editorial brief). A writer drives a web wizard; the tool assembles
offer facts, live odds, and event context, generates an outline and draft with an LLM,
runs a large chain of deterministic quality passes over the output, and validates the
result against compliance rules. Live at
https://topstories-api-production.up.railway.app.

## The one design rule, and where it came from

**Facts are rendered deterministically from source data; the LLM writes connective prose
only.** This is not a style preference — it is the distilled conclusion of months of
editorial feedback, and every incident in the git history where a published number, state
list, price, or age was wrong traces to the model being allowed to restate a fact:

- A "$15 in promo credits" offer published as "$10 in promo credits" — the model (and,
  earlier, the amount parser) confused the qualifying amount with the reward
  (commit `24b4267`).
- The footer said 21+ while the article's own terms section said 18+ — a hardcoded
  sportsbook disclaimer pasted onto a prediction-market article (`24b4267`).
- An availability list read "AL, AK, AR, CA … MN, MS, and MO" and just stopped — the
  model was reciting US states from its own world knowledge, alphabetically, and ran out
  of steam at Missouri, right before Montana (`dc8a397`).
- The editor preview showed contract math at "$0.50 per contract" for a market nobody had
  selected — a hardcoded placeholder price rendered as if it were a real quote (`c01a0ca`).
- A Tuesday MLB article carried Monday night's game facts — a UTC date window matched the
  same team names one game early (`d7ecbb8`, see the ET-day section below).

The corollary, which the codebase keeps re-proving: **models paraphrase around ban
lists.** A prompt rule alone has never held. So every ban has a deterministic regex
backstop in the postprocess chain, and every fact the model keeps mangling eventually
gets moved into a deterministic renderer where the model never touches it. When you
extend the tool, keep fact-carriers (worked examples, terms, signup steps, quick facts,
state lists) out of the model's hands.

## Architecture at a glance

FastAPI + Jinja2/HTMX/Alpine (no SPA build). ~20,900 lines under `app/`, dominated by
`app/services/draft.py` (6,765 lines — see Known debt). SQLite by default
(`storage/planwrite.db`), Postgres-compatible via `DATABASE_URL`. Session-cookie auth.

```
Browser (article/new.html wizard)
  │  SSE
  ▼
/api/generate/outline ──► outline.py (LLM structured outline)
  │                       └─ GOAL: goal_template.build_goal_outline (deterministic, no LLM)
  ▼
/api/generate/draft ────► draft.py generate_draft_from_outline
  │     per section: intro → quick-facts table → shortcode (BAM block)
  │                  → body sections (LLM) with deterministic renderers for
  │                    worked examples / signup steps / terms / promos
  │     then: main postprocess (~17 passes) → links → disclaimer → humanizer
  │           → late-appended sections (analysis / length / promos)
  │           → late strip chain (~14 passes) → GOAL footer → tracking tag
  ▼
/api/generate/validate ─► compliance.validate_content (9 checks, scored)
```

The output pipeline deliberately has **two cleanup layers** — the main postprocess and a
"late chain" — and the reason is not elegant. See "The output pipeline in detail" below;
understanding that section is the single highest-value thing in this document.

Every run persists its stages under `storage/generation_runs/<date>/<run_id>/`
(`00_request`, `10_source_facts`, `20_outline`, `30_draft`, `40_validation`,
`manifest.json`) via `app/services/generation_artifacts.py`. When debugging "why did the
article say X", start from `10_source_facts.json` — it is the single record of every fact
that was fed to prompts. If a number in the article is not in that file, the model
invented it and a postprocess pass or renderer should have caught it. (Caveat: only the
`/sync` endpoints write artifacts — see Known debt.)

### Entry points

- `app/main.py` — app factory, auth middleware (`AuthenticationRequiredMiddleware`),
  page routes (`/articles/new` wizard, `/articles/{id}` editor, `/admin`, `/login`,
  `/health` public).
- `app/api/generate.py` — the pipeline: `POST /outline`, `POST /draft` (both SSE, with
  `/sync` variants), `POST /validate`, `GET /runs/{run_id}`, `GET /link-options`.
- `app/api/offers.py` — BAM catalog (list/filter/sync/by-id).
- `app/api/events.py` — ESPN games per sport/date, featured game.
- `app/api/odds.py` — odds for the bet builder (Charlotte + BC Core, see Data sources).
- `app/api/prediction_markets.py` — Kalshi/Polymarket market search + example builder.
- `app/api/articles.py` — article CRUD + version snapshots.
- `app/api/admin.py` — link curation, index rebuilds, usage audit/export.

### Services catalog

| File (lines) | Role |
|---|---|
| `draft.py` (6,765) | The draft engine: section generators, deterministic renderers, humanizer, all postprocess passes. |
| `outline.py` (1,824) | LLM structured outline, editorial section rules, question-mapped PM headings, token/text conversions. |
| `expertise_context.py` (1,230) | Lineup/absence/form/weather notes from BC Core for editorial points. |
| `internal_links.py` (912) | Per-property evergreen link indexes (embeddings), suggestion/required/picker APIs. |
| `bam_offers.py` (879) | BAM offer fetcher, pickle caching, state normalization, switchboard URLs. |
| `compliance.py` (785) | All validation checks incl. `check_offer_consistency`, state disclaimers, scoring. |
| `prediction_markets.py` (694) | Kalshi/Polymarket search + example-text builder. |
| `bc_core.py` (637) | BC Core client: operator context, event matching, ET-day date windows, prompt summaries (via Tailscale SOCKS). |
| `odds_fetcher.py` (555) | Charlotte/RotoGrinders odds client + bet-option/example builders. |
| `offer_parsing.py` (487) | Regex extraction from offer text/terms: amounts, states, exclusions, min age, expiry. |
| `event_fetcher.py` (333) | ESPN schedules (soccer sweeps ten league scoreboards), featured-game pick. |
| `goal_template.py` (328) | GOAL deterministic outline, terms table, sticky CTA + page-blocker footer. |
| `bc_core_odds.py` (308) | BC Core market prices → `/api/odds/game` shape (soccer coverage Charlotte lacks). |
| `generation_artifacts.py` (292) | `source_facts` builder + per-run artifact dirs. |
| `rag.py` / `rag_builder.py` | FAISS style-example retrieval (style only, never facts). |
| `llm.py` (206) | AsyncOpenAI wrapper: chat, streaming, JSON mode, embeddings. |
| `usage_tracking.py` (202) | Login/API usage events, summary, CSV export. |
| `switchboard_links.py` (199) | Affiliate tracking link injection. |
| `bc_core_boosts.py` | Live odds boosts (GOAL "promos today" block). |
| `quick_facts.py` (126) | Deterministic quick-facts table (code/trigger/reward/age/event/states). |
| `operator_profile.py` (103) | Content-mode decision (sportsbook / prediction_market / dfs). |
| `operator_facts.py` (100) | Curated overrides where feeds are thin (e.g. Underdog age rules, sport-scoped standing promos). |

### Content modes and properties — two orthogonal axes

- **Content mode** (`operator_profile.py`): operator name → `sportsbook`,
  `prediction_market` (Kalshi, Polymarket, Novig), or `dfs` (Sleeper, Underdog, Dabble).
  This exists because the three product types have incompatible vocabularies and legal
  postures: a prediction-market article that says "bet", "odds", or "sportsbook" is
  factually and legally miscategorizing the product (these are CFTC-regulated exchanges,
  not books). The mode controls language guardrails
  (`_apply_content_mode_language_guardrails` deterministically rewrites stray sportsbook
  vocabulary), selects the deterministic PM/DFS renderers, adapts the disclaimer, and
  switches heading style (PM articles get question-shaped headings — "How do you use the
  promo credits?" — because that is what readers and LLMs actually search for).
- **Property** (`offer_property` request field): `action_network` (default) vs `goal_com`.
  `is_goal_property` gates a completely different article shape driven by GOAL's own
  brief (the "Ultimate SEO Guide" sheet): deterministic outline with **no LLM outline
  step at all**, ≤150-char intro, numbered how-to under a mandated heading, one
  match-analysis H3 that carries all the tactical content, terms table, sticky CTA
  (placement 2066 / property 326 — each property's CTA uses that property's own BAM ids,
  Action is 2037/1) plus a US-compliance page blocker, ~550–600 word target. GOAL
  articles do NOT get the quick-facts table (their brief has its own terms table), and
  their body copy must never address readers by state (GOAL is a national site — see
  `_strip_state_callouts_from_goal_body`).

The two axes multiply: a GOAL soccer article and an Action Network Kalshi article share
almost no code path beyond the orchestrator skeleton and the postprocess chains.

## Data sources and migration state

| Source | What it supplies | State |
|---|---|---|
| **BC Core** (`bc_core*.py`, `expertise_context.py`) | Events, lineups/absences/form/weather ("expertise" editorial points), operator context, odds boosts, market prices incl. soccer 1X2 | Primary and only source for everything except US odds |
| **Charlotte / RotoGrinders** (`odds_fetcher.py`) | US sportsbook odds for the editor's bet builder | Still primary for US odds; BC Core is the fallback |
| **BAM** (`bam_offers.py`) | Offers, codes, terms, states, switchboard links | Primary, pickle-cached 6h, schema v3 |
| **Polymarket/Kalshi** (`prediction_markets.py`) | Prediction-market search + real contract prices | On-demand, in-memory cache |
| **ESPN** (`event_fetcher.py`) | Game schedules for the UI picker; soccer merges ten league scoreboards | Plain HTTPS, no key |
| **OpenAI** (`llm.py`) | Drafting + embeddings (`gpt-5.5-2026-04-23`) | Only provider; model-prefix shims in `llm.py:33` |

**Why Charlotte is not retired (a hard constraint, not laziness):** BC Core's
`/sportsbooks` has **no DraftKings** (verified against all 472 books in its catalog).
DraftKings is a book writers need in the bet builder, so Charlotte — which has it — cannot
be dropped. BC Core adds what Charlotte lacks entirely: FanDuel and all soccer (every
soccer fixture returns null from Charlotte). BC Core moneyline/total parity vs Charlotte
was verified live before trusting it (exact match across four books). So the odds path
is: Charlotte first, BC Core fallback — both when Charlotte cannot find the game at all
(`app/api/odds.py:64`) and when it finds the game but carries no board for it
(`app/api/odds.py:129`). Critically, `bc_core_odds.py:131` maps BC Core markets into the
**identical response shape** as `/api/odds/game`, so the day BC Core lists DraftKings,
the swap is mechanical — flip the order, delete the client. That shape-matching was a
deliberate migration investment, not an accident.

**The ET-day event window** (`bc_core.py:417`, `_date_range_params_for_event`) deserves
its own paragraph because it fixed a real published error. BC Core event endpoints are
queried with a start/end window derived from the requested game's date. The window used
to be the game's **UTC day**. But a US game day is an Eastern Time concept: an 8:10 PM ET
Monday game is already Tuesday in UTC. In an MLB series the same two teams play on
consecutive days, so team-name matching inside a "Tuesday" UTC window happily matched
**Monday night's game** — and Monday's lineups, weather, and trends were published in
Tuesday's article (Nick's biweekly, 2026-07-21). The fix: the window is the requested
game's ET calendar day (bare dates from the UI are interpreted as ET days), converted to
UTC only at the edges. If you ever touch event matching, preserve this: date windows are
ET-day windows.

**Half-migrated edges the next team should know:**
- **Dual transport remnant** in `bc_core.py:16-17`: public host + legacy proxy host both
  configured; the Tailscale SOCKS path is what production actually uses.
- Stale hardcoded default week `"2025-reg-13"` in `odds_fetcher.py:74,152` — harmless
  while callers pass explicit dates, but a trap for a new caller relying on the default.
- **Player props / closing lines**: recon complete, unimplemented (see Open threads).
- Two latent bugs found in the handoff audit were **fixed** in `f0066b3`: the soccer
  picker was hardcoded to ESPN's `fifa.world` scoreboard and would have gone dark the day
  the World Cup ended (it now sweeps MLS, Liga MX, the top-five European leagues, UCL,
  UEL, and the World Cup feed concurrently, deduped by event id — verified live at 15
  fixtures vs zero from the old feed); and college boost lookups silently returned
  nothing because the app says `ncaaf`/`ncaab` while BC Core's boost paths say
  `ncaafb`/`ncaamb` (a sport-alias map now bridges them). They are listed here because
  they are the shape of bug this codebase grows: **a data lookup that returns empty is
  indistinguishable from "nothing scheduled today"** — nothing logs, nothing fails, the
  article is just thinner. When output looks mysteriously thin, suspect this class first.

## The output pipeline in detail

This is the section the previous version of this document compressed into two lines, and
it is where the real editorial value lives. Read it before touching `draft.py`.

### Assembly order

`generate_draft_from_outline` (`draft.py:4880`) walks the outline: intro (LLM inside a
fixed lede template) → quick-facts table (deterministic, PM only) → BAM shortcode block →
body sections. Each body section is routed by its **heading text** to either a
deterministic renderer (worked example, signup steps, terms, daily promos) or the generic
LLM section writer — see "Section routing" below for why that routing is fragile.

Then, on the joined article, in this order (`draft.py:5042-5171`):

1. **Main postprocess** — `_apply_generation_quality_postprocess` (`draft.py:3725`),
   ~17 passes, detailed below.
2. Internal-link work: first-paragraph keyword link, then the single disclaimer, then
   switchboard (affiliate) link injection capped at one, then stripping/deduping/aligning
   all non-switchboard links against the writer's selections. Links happen here because
   they need clean, final prose to anchor into.
3. **Humanizer** — an LLM polish pass over safe prose sections only, with every fact
   protected by placeholder (detailed below). Runs after links so it cannot orphan an
   anchor.
4. Keyword passes (density floor/ceiling, bolding), content-mode language guardrails,
   brand casing in the keyword.
5. **Late-appended sections**: for non-GOAL articles, `_ensure_matchup_analysis_section`
   and `_ensure_editorial_body_length` may append a data-led "What the Numbers Say"
   section; for GOAL, the operator-promos section is inserted before the terms.
6. **Late strip chain** (`draft.py:5144-5162`): projection strip, priceless-pick strip,
   starter-count fix, formation dedup, meeting/score dedup, GOAL state-callout strip,
   vacuous-qualifier strip, clock style, market-percent nouns, state-enumeration
   collapse, keyword-density cap, search-query-opener strip, heading title case, brand
   casing.
7. GOAL footer (sticky CTA + page blocker), tracking tag.

### Why there are two cleanup layers — the honest version

The late-appended sections in step 5 are the reason the late chain in step 6 exists.
They run after the main postprocess because they are **conditional on properties of the
finished article**: the length section only fires when the assembled editorial body is
under ~500 words (`_ensure_editorial_body_length` counts words excluding signup/terms),
and the analysis section only fires when no analysis-shaped heading already exists and
enough unused BC Core facts remain to build one (`_build_length_expansion_section`
requires an event label and at least two editorial points). You cannot know either of
those things until the article is fully assembled. There is also a tail of steps that
must be last regardless: link injection wants final prose, the humanizer must run after
links (with facts protected), and the disclaimer and link caps count article-wide.

But be honest about the rest: **this is accretion, not design.** The late sections were
added months after the main postprocess existed, and the late chain is a patch over the
first layer's blind spot — anything appended after the main postprocess simply never went
through it. That was discovered the hard way: the projection ban shipped, tested green,
and model projections still reached GOAL copy **through the appended analysis section**,
which the main postprocess had never seen (commit `3dd25af` closed this by re-running the
projection strip late). Since then, every pass that protects a hard editorial rule gets
wired into the late chain too (the clock-style fix in `24b4267` was added to both layers
on day one, having learned the lesson). The clean design would be a single gauntlet that
runs once after full assembly, including appended sections; nobody chose two layers, and
consolidating them is a worthwhile refactor — but do it by moving the late-append steps
earlier, not by deleting the second layer and reopening the bypass.

### The passes, with the incidents behind them

Grouped by what they protect. Each entry: why it exists, what it does. File refs are into
`draft.py` unless noted.

**Availability language** — the model must never enumerate or label states.

- `_convert_availability_labels_to_prose` (`:2604`): the model copied the prompt's
  internal data format into copy, publishing literal labels. "States Available: AZ, CO."
  → "The offer is available in AZ, CO." A "listed by the operator" placeholder value
  becomes "Availability varies by state, so confirm eligibility during signup." (Canada:
  provinces.)
- `_collapse_state_enumerations` (`:746`): the truncated-list incident. The
  deterministic path emits no list at all for nationwide offers, so a recited list could
  only come from the model's own knowledge — alphabetical by state name, stopped at MO
  before Montana, never reconciling with the 8 real exclusions. Any surviving run of
  state codes in a **nationwide** offer's copy is replaced with the offer's complete
  statement: "all US states except AZ, IL, MA, MD, MI, MT, NV, OH". Two deliberate
  scope limits: offers with a real named state set keep their enumeration (it is
  correct), and text in an exclusion context is left alone (an exclusion list is short,
  complete, and true).
- `_remove_generic_state_fallbacks` / `_ensure_intro_state_specificity`: drop vague
  "eligible states listed by the operator" filler when a real list exists nearby; make
  sure the lede names the state(s) as prose, but never dump a >3-state list into the lede
  (it reads like a data dump — the full statement lives in quick facts/terms).

**Model-number bans** — numbers the model produced, as opposed to numbers we sourced.

- `_strip_projection_sentences` (`:2051`): BC Core's feed includes model projections
  ("projects for 7.5 strikeouts", "projects for 68.45 passes"). Editorial rule: **model
  projections never publish** — only posted market lines and real historical stats. The
  points are filtered out upstream (`_select_bc_core_editorial_points` drops the
  projection category), the category classifier runs projection-first so "pitching outs"
  can't substring-match the injury token "out" and smuggle one through (that exact
  misclassification published a banned number — commit `3dd25af`), and this pass deletes
  any surviving "projects for" sentence as the backstop of last resort. It runs in both
  layers because the analysis section once bypassed the first.
- `_strip_priceless_market_picks` (`:2194`): a GOAL draft said "Portugal draw no bet is
  the natural starting point" — a named pick with no posted price, which reads as a
  recommendation with fabricated authority. Editorial rule: **never name a market or
  pick without its price.** The talking points were reversed (with no posted prices, the
  model is told to argue from form/tactics and never name a market), and this pass drops
  any sentence naming a market ("draw no bet", "anytime goalscorer", "both teams to
  score", over/under goals) that carries no price token (+150 / 62¢ / $0.62).
- `_strip_quoted_stat_phrases` (`:2275`): the model quoted internal notes verbatim, with
  quotation marks, making internal data language visible ("projects for", "% of
  handle"). Unwraps the quotes; the other passes then judge the content.

**Repetition dedup** — one fact, one mention; models restate facts in new clothes.

- `_dedupe_formation_mentions` (`:2089`): GOAL feedback — the same 4-2-3-1 was named in
  three sections. First mention keeps the numbers; later repeats become "that shape".
  A follow-up pass collapses the "same 4-2-3-1 shape" → "same shape shape" artifact this
  creates (`541dec7`).
- `_strip_starter_count_phrases` (`:2065`): "Both teams list 11 starters" — a soccer XI
  is always 11, so this states nothing. Rewritten to keep only the formation ("Both
  teams line up in the same 4-3-3").
- `_dedupe_latest_meeting_sentences` (`:2125`): the head-to-head result is one fact.
  Because models paraphrase ("latest meeting" → "most recent meeting" → a bare "2-1
  win"), deduping by phrase is not enough — this pass also dedupes **by the score token
  itself**: a later result-sentence repeating an already-used score is dropped. This
  "dedupe by fact token, not phrasing" idea is the same one behind the GOAL fact-once
  filter (`_bc_core_point_facts_already_used`, `:294`), which marks a BC Core point as
  used when its compound numbers or player surnames already appear in earlier sections —
  regardless of wording.

**CTA hygiene.**

- `_strip_vacuous_qualifiers` (`:2223`): the model kept hedging the CTA — "use the code
  when relevant", "where applicable", "if it makes sense". You use the code when you
  sign up; the qualifier says nothing (`0c9bd45`). The pass removes when/where/if/as +
  vacuous-head-word clauses; a real temporal clause ("when the market settles") is
  untouched. This pass is also the site of the **ACTIONdeposit** lesson: transforms see
  one HTML text node at a time, and `<strong>ACTION</strong> when relevant, deposit $20`
  hands the transform a node that *starts* mid-sentence — its sentence-initial comma
  cleanup then ate the separating space, publishing "ACTIONdeposit $20". Third recurrence
  of the text-node-splitting bug class. The fix lives in the shared helper:
  `_rewrite_html_text_nodes(..., with_block_context=True)` (`:2355`) tells a transform
  whether it is at a real block start or merely following an inline tag. Use it for any
  new sentence-boundary-sensitive pass.

**Noun precision.**

- `_normalize_market_percent_nouns` (`:2256`): BC Core market-percents are ticket
  and handle splits — two different denominators. The model paraphrased both into
  "80% of bets", which is verifiable as neither; Nick could not confirm the number and
  **deleted true data** from a published article (2026-07-21 biweekly). The pass pins
  the noun: "80% of bets" → "80% of tickets" (tickets = count of wagers, the dominant
  source phrasing). The naturalizer that turns raw feed notes into prose keeps the
  precise noun for the same reason.

**Style normalization.**

- `_normalize_clock_time_style` (`:2263`): one article carried both "3:00 PM ET" and
  "3:00 p.m. ET" — the deterministic event line uses the first, the model drifts to the
  second. Normalized to the house "3:00 PM ET". In both layers since day one.
- `_cap_primary_keyword_density` (`:2929`): SEO target is 5–9 exact keyword mentions.
  The floor is handled during generation (per-section budget); this pass handles the
  ceiling: the 10th+ plain-text mention is converted to brand-only text ("bet365 bonus
  code" → "bet365"), skipping mentions inside links or bold. There is deliberately NO
  keyword-filler machinery to force the floor — it was removed as spam; do not
  reintroduce padding.
- `_title_case_headings` (`:2970`) then `_normalize_brand_casing` (`:3018`): house title
  case on h1–h3 without touching acronyms, codes, or anything with digits — then brand
  casing is restored on top because title-casing "bet365" would produce "Bet365", and
  bet365 is always lowercase in copy.
- `_strip_search_query_openers` (`:2006`): the lede kept opening by addressing people
  searching the keyword ("Readers checking the DraftKings promo code can find…") — an
  SEO-copy tell. The prompt bans it, the models paraphrase around the ban, so the regex
  strips reader-addressing and colon-preamble openers from the first paragraph.
- Assorted intro/example polish: `_soften_repetitive_intro_opener`,
  `_polish_worked_example_conditionals`, `_normalize_matchup_vs_notation`,
  `_decapitalize_inline_reward_mentions` (shouty offer-headline casing — "in Bonus Bets
  Instantly" — lowercased mid-sentence, but left alone in headings and in the terms
  block, which quotes the operator verbatim), `_strip_source_and_prompt_leaks` (internal
  labels like "BC Core" must never appear in copy), `_strip_market_mismatch_phrasing`
  (US phrasing in CA-market articles and vice versa).

### The humanizer — an LLM pass that cannot change a fact

`_humanize_article_html` (`:3917`) exists because a fully deterministic pipeline reads
tool-shaped; Nick praised "first-hand texture" and the drafts lacked it. It is the one
place an LLM touches assembled copy, so it is caged:

1. The article is segmented (`:3860`); only intro and body **prose** blocks are
   rewriteable. Signup steps, claim/worked-example sections, daily promos, terms, lists,
   tables, and headings are static — they are fact-carriers.
2. In each rewriteable block, every `<a>`, `<strong>`, `<em>` fragment is replaced by an
   opaque `[[KEEP_n]]` placeholder before the model sees it (`:3757`) — it cannot alter a
   link, a bolded code, or their text even by accident.
3. After the rewrite, `_humanizer_preserves_markers` (`:3821`) rejects the block unless:
   paragraph count is unchanged, no headings/lists/tables were added, every placeholder
   survived, and every **hard-fact marker** extracted from the original — dollar
   amounts, odds tokens, 18+/21+/19+ ages, clock times, dates, state lists,
   availability sentences, network names, the bonus code — still appears verbatim in the
   rewrite. Any failure means that block silently keeps its original text.

So the humanizer can reword a sentence but cannot change a number while doing it; the
worst it can do is nothing. Note the asymmetry with the late-appended sections: those are
composed after the humanizer runs, so they are never humanized — they are written by a
validated-narrative composer instead (`_narrative_section_is_valid`, `:3169`, which
rejects any draft whose numbers are not a subset of the provided facts).

### The deterministic renderers (fact-carriers)

- **Quick-facts table** (`quick_facts.py`, PM articles only, directly under the lede):
  code, offer, trigger, reward, age, event, eligible states, expiry. Every row is
  **omitted rather than guessed** — an absent fact must not become an invented one. It is
  both Nick's requested citable block and the structural fix for the state-list recital:
  "All US states except …" rendered from offer data is the complete format prose
  enumeration never achieved.
- **Worked examples** (three renderers, `:4648` PM / `:4758` sportsbook / `:4829` DFS):
  the money must trace end to end. PM: "I complete the $10 qualifying action → that
  triggers $15 in promo credits → the credits fund the position." A contract price
  prints **only** when a real matched market supplied one — the UI bet-builder used to
  hardcode $0.50 and the preview printed full contract math for it, which is fabrication
  (`c01a0ca` removed it; the payload no longer even carries `entry_price` without a
  market). Without a price there is no contract math at all, and without a named side
  the money goes "into an eligible market on X" — you cannot back a whole fixture.
  Handing the model a rendered example "as a hint" was tried and reverted: it rewrote
  the amounts (a $10 qualifying trade became a "$50 position"), so the deterministic
  example publishes as-is. Sportsbook examples must not show total-payout figures
  (profit-only; tests assert it).
- **Signup steps**: structured generation with a deterministic fallback list; the code
  entry step renders the code bolded from offer data.
- **Terms**: rendered from the offer's own terms text; GOAL gets its brief's 3-row
  table.
- **Disclaimer**: one per article, appended last, state-keyed. For prediction markets
  the default 21+ assertion is replaced by the age **sourced from the operator's own
  terms** (`extract_minimum_age`; Kalshi/Polymarket terms say 18+) — and when the age is
  unknown the disclaimer asserts nothing rather than guessing. This is the fix for the
  18+/21+ contradiction.
- **GOAL package** (`goal_template.py`): the whole outline (no LLM), the terms table,
  the sticky CTA and page blocker with GOAL's BAM ids, and same-day sibling titles fed
  into the outline as "do not reuse angles" constraints to avoid cannibalization.

## Section routing: how a heading becomes a renderer

Body sections are routed to their renderers by **matching English heading text**:
`_is_signup_heading` (`:591`), `_is_claim_heading` (`:604`), `_is_daily_promos_heading`
(`:619`), and an inline `is_terms` substring check (`:5823`), with a parallel classifier
set in `outline.py`. This is the most fragile joint in the codebase, and here is the
specific failure mode: the outline stage already computes a real `section_kind` for every
H2 (`outline.py:520`, `_classify_h2_section`) — and then **throws it away**, keeping only
the (possibly rephrased) title. The draft stage re-derives the kind from the title. So if
anyone — the model, a writer editing the outline text, a future title A/B test — words a
heading outside the substring lists, the section silently downgrades from its
deterministic renderer to generic LLM prose, and the first symptom is an editorial
regression weeks later. The right fix is plumbing `section_kind` through the outline
payload into `_generate_body_section`; it has not been done. Until it is, treat heading
wording as load-bearing.

## Known debt and jank

An honest inventory, tiered by how much it will hurt you.

### Structural (will hurt immediately when you start changing things)

- **`draft.py` is a god-module**: 6,765 lines, ~219 functions — prompts, ~80 regex
  postprocess passes, renderers, humanizer, markdown conversion, and both orchestrators
  in one file. Why it hurts: every change forces you to reload the whole mental model,
  merge conflicts concentrate here, and test failures point into a haystack. Carving out
  `postprocess.py`, `renderers.py`, and `humanizer.py` would be mostly mechanical.
- **The two orchestrators are ~95% copy-pasted** (`generate_draft_from_outline`
  `draft.py:4880` vs `generate_draft_from_outline_streaming` `:6306`, ~290 lines each;
  the same again in `app/api/generate.py`, where the offer-fetch/source-facts/enrich
  block repeats four times across the SSE and sync endpoints). Why it hurts: every
  pipeline fix must be applied twice, and the copies have **already drifted** in two
  observable ways. First, the `is_terms` heading list differs between paths —
  `draft.py:5823` includes "house rules/market rules/settlement", `:6632` does not — so
  the same outline can route a "Market Rules" section to the terms renderer on one
  endpoint and to generic LLM prose on the other. Second, **the streaming paths write no
  generation artifacts** (only the `/sync` handlers call `create_generation_run`), so
  whether a run is debuggable depends on which endpoint the UI happened to hit. Unify
  before extending either.
- **Section routing is English-string matching** — see the dedicated section above. The
  computed `section_kind` being discarded at the outline/draft boundary is the root
  cause; plumbing it through is the fix.
- **Single-replica by construction**: SQLite, module-level in-process caches
  (`bam_offers` with a 6h TTL, `prediction_markets`, and `bc_core_odds` — the latter's
  book-id caches have **no TTL at all**, so a book added upstream is invisible until
  redeploy), and filesystem artifact reads (`list_same_day_run_titles`, `GET
  /api/generate/runs/{id}` read `storage/` directly). Why it hurts: a second replica
  would see different caches, different run artifacts, and a SQLite file it cannot
  share. Scaling out requires Postgres + a shared cache + object storage first; at the
  current load (a handful of writers) this is fine and not worth pre-building.

### Latent bugs and dead code (audit findings, current at `d7ecbb8`)

- ~~`ncaaf`/`ncaafb` boost key mismatch~~ and ~~soccer picker dies after the World Cup~~
  — both **fixed** in `f0066b3` (sport-alias map; ten-scoreboard soccer sweep). Left
  here so you do not re-discover them in old notes.
- `_html_to_markdown` (`draft.py:6284`) parses HTML with regex; it handles `<ol>` but
  not `<ul>` (unordered lists silently lose their markers, every `<li>` becomes "1."),
  and attributes on `<p>` tags break its paragraph regex. It only matters on the
  markdown output path, which the streaming endpoint uses.
- Legacy token pipeline (`parse_token` `:6596`, `generate_draft` `:6694`,
  `generate_draft_streaming` `:6734`) has **zero callers** — delete on sight, but note
  the `is_terms` divergence partly lives here, so delete it as part of the
  orchestrator unification.
- Dead code: `GOAL_TARGET_WORDS` (`goal_template.py:18`, never read),
  `render_bam_offer_block` (imported in `draft.py:25`, never called),
  `build_bet_options` (imported in `app/api/odds.py:11`, never called),
  `inject_brand_links` (`switchboard_links.py:117`, no callers), `app/workers/` (empty
  package). Each is a small landmine for a newcomer who assumes imported = used.

### Operational hygiene

- **Nothing cleans `storage/`** — there is zero cleanup code in the app. Today: 97
  pickles across three cache-schema generations (only v3 is read; v1/v2 files are pure
  dead weight kept alive by nothing but inertia), 128 ad-hoc QA HTML files in
  `storage/manual_checks/`, and `generation_runs/` grows one directory per run, forever.
  Why it hurts: on Railway this is container-local disk; growth is slow but unbounded,
  and the stale cache generations make "which file is the app actually reading" a
  research question. A startup sweep or cron would close it.
- **62 broad `except Exception` across 22 files**, many returning `{}`/`[]` silently.
  Why it hurts: a BC Core outage does not error — it degrades. Articles come out with no
  odds, no boosts, no expertise points, and nothing in the logs says why, because every
  fetch failure was swallowed into an empty default. This is a deliberate availability
  trade (a data blip should not 500 the writer's draft), but it means: when output looks
  mysteriously thin, suspect a swallowed upstream failure **first**, and check
  `10_source_facts.json` for `"matched": false` entries with a `reason`.
- `datetime.utcnow()` (deprecated in 3.12+) in `bam_offers.py` and
  `usage_tracking.py`; the rest of the codebase uses `datetime.now(UTC)`. `print()`
  instead of structlog in `odds_fetcher`, `event_fetcher`, `bam_offers` — those modules'
  failures are invisible in structured log search.
- Pickle as the BAM cache format — schema-brittle (the v2→v3 migration scar above) and
  an arbitrary-code-execution risk if storage were ever shared or writable by another
  process. JSON would do.
- `uv.lock` is **untracked** while `pyproject.toml` is tracked — builds are not
  reproducible until it is committed. Given the 3.11/3.13 skew below, this is riskier
  here than in most repos.
- Repo-root clutter: ~25 untracked one-off manifest/compare scripts and outputs from a
  data-sync exercise, a stale `.railway-deploy-current/` source snapshot, and ~50 stale
  deploy worktrees under `%TEMP%` on the dev machine. None of it is load-bearing except
  `docs/charlotte-odds-api.md` and `scripts/create_bc_brief_doc.py` (which embeds a
  personal-machine token path) — both untracked; commit the doc, sanitize the script.
- Easy performance wins left on the table: `_operator_promos_for_draft` and
  `_operator_boosts_for_draft` are awaited sequentially (`app/api/generate.py:684-685`)
  when they are independent, and `get_offer_by_id_bam` re-fetches the whole catalog per
  alt-offer id instead of once per request.

### Where the quality actually lives

The editorial/output layer is the part months of feedback hardened: deterministic
fact-carriers, the two postprocess chains, the 330 tests, the compliance checks. Trust
it, and extend it in its own idiom (deterministic backstop for every rule). The
infrastructure layer is a solo-built internal tool that has never needed to scale — fine
at one replica and a handful of writers, but it is where a new team will feel friction
first, and none of its debt is subtle: it is all listed above.

## Operational runbook

### Local dev

- Local venv is **Python 3.13**; production image is **python:3.11-slim**. This skew has
  caused a production outage once: PEP 701 f-strings (nested same-quote) parse on 3.13,
  pass all tests locally, and crash the app **on import** in 3.11 — a 502 behind a
  SUCCESS deploy banner.
- Tests: `.\.venv\Scripts\python.exe -m pytest` (NOT global python). 330 passing at
  `d7ecbb8`.
- **Before every deploy**: `python scripts/check_py311_grammar.py` — parses all of
  `app/**/*.py` with `feature_version=(3,11)`. Non-negotiable given the skew above; it
  is the only thing standing between a 3.13-only construct and the import crash.

### Deploy (Railway)

Project `planwrite-v2-topstories` (`b722d2ce-2e5b-4529-a4c4-14963ec85eab`), environment
`production` (`53e36246-d009-485a-b585-2e3aa2a5eabf`), service `topstories-api`
(`5152a3d3-dd85-4b64-b026-133c9c9dfe32`). Login creds live in Railway vars
(`AUTH_USERNAME`/`AUTH_PASSWORD`; fetch via `railway variables --json` from a linked dir).

Trusted procedure (deploys exactly what is committed, never the dirty working tree —
`railway up` uploads the directory it runs in, so a detached worktree is the guarantee):

```
git worktree add --detach $TEMP/planwrite-v2-deploy-<sha> HEAD
cd $TEMP/planwrite-v2-deploy-<sha>
railway link --project b722d2ce-... --environment 53e36246-... --service 5152a3d3-...
railway up --detach
```

Then verify the app is actually serving — `railway status` does not report deployment
health usefully. `curl -s -o /dev/null -w "%{http_code}" https://topstories-api-production.up.railway.app/`
should return **302** (auth redirect to `/login`; that is healthy). 502/503 means still
building or an import crash — check `railway logs --deployment` for
`Application startup complete`. Remove the worktree afterwards
(`git worktree remove <dir> --force`).

BC Core is only reachable from Railway through a Tailscale userspace tunnel
(`railway-entrypoint.sh` boots `tailscaled` with a SOCKS5 proxy on `127.0.0.1:1055`;
`TS_AUTHKEY`/`TS_HOSTNAME` vars; see `docs/hetzner-tailscale-tunnel.md`). If BC Core
data vanishes from articles after a deploy, check the tunnel came up before suspecting
the API.

### LLM (gpt-5.5) gotchas, burned in production

- Model: `gpt-5.5-2026-04-23` (Railway var + `LLM_MODEL` default). It is a
  reasoning-first model and **rejects non-default temperature** — the request would 400.
  `_sampling_params` (`llm.py:46`) therefore omits temperature for `gpt-5.4`/`gpt-5.5`
  prefixes and sends `reasoning_effort="low"` instead (valid values:
  none/low/medium/high/xhigh; `"minimal"` is NOT valid and 400s). Callers throughout the
  codebase still pass `temperature=` — it is silently dropped for these models, which is
  intended, but know that tuning those numbers currently does nothing.
- **Reasoning tokens are billed out of `max_completion_tokens`** — the model "thinks"
  invisibly inside the completion budget. `_token_param` adds +1500 headroom; without
  it, intros truncate mid-sentence, the truncation trips the fallback, and the
  deterministic fallback ships visibly templated copy. Keep the headroom.

### Editorial conventions that look like bugs but are deliberate

- Availability is prose, never a "States Available:" label, and nationwide-with-
  exclusions renders as "all US states except …" — never an enumeration. The model
  truncates lists it recites (the MO/Montana incident); the exclusion list is the only
  list short enough to always survive intact, so it is the one that renders.
- Worked examples must NOT show total payout figures for sportsbook articles
  (profit-only figures are OK; tests assert this). Prediction-market examples only print
  a contract price when a real matched market supplied it — a fabricated price reads as
  a real quote.
- bet365 is always lowercase in copy, enforced after title-casing. Branded reward
  currencies ("Novig Coins") keep their casing; generic shouty labels ("Bonus Bets
  Instantly") are lowercased mid-sentence but preserved in headings and in the terms
  block, because terms quote the operator verbatim.
- Primary keyword density target is 5–9 mentions; overage converts to brand-only text.
  There is deliberately NO keyword-filler machinery to force the floor — it existed, it
  produced spam, it was removed; do not reintroduce padding to hit density.
- Headings h1–h3 are title-cased in postprocess, then brand casing is restored on top.
- The article lede has ONE canonical shape (the `LEDE TEMPLATE` in the intro prompt,
  `draft.py:5581`): the shape is fixed, only the facts and connective wording vary. Do
  not rotate opener styles — variation lives in the section-level "variation briefs",
  not the lede.
- GOAL body copy never addresses readers by state ("In Kentucky, …") — GOAL is a
  national site; the legal-states line in the how-to steps and the terms table are the
  only sanctioned state mentions.

### Engineering lessons this codebase keeps re-teaching

Written down because each has caused at least one production defect:

1. **Models paraphrase around ban lists.** A prompt rule alone has never held. Every ban
   needs a deterministic regex backstop in the postprocess chain, and every fact the
   model keeps mangling eventually needs a deterministic renderer instead.
2. **Inline tags split text nodes.** `<strong>` mid-sentence means your sentence-level
   regex sees two fragments, and "start of node" stops meaning "start of sentence". This
   class of bug has shipped three times (the "ACTIONdeposit" glitch being the third).
   `_rewrite_html_text_nodes(..., with_block_context=True)` exists so transforms can
   tell a real block start from a mid-sentence node — use it.
3. **Word boundaries in classifiers.** "out" substring-matched "pitching outs" and
   misclassified a projection as injury news, publishing a banned number past a passing
   test suite. Categorize with word boundaries, and run the most dangerous category's
   check first.
4. **Section routing keys off heading text.** Changing a heading can silently downgrade
   a section from its deterministic renderer to generic LLM prose. The outline already
   computes `section_kind` and throws it away — plumb it through.
5. **Verify category filters against live output**, not just unit fixtures. The
   projection ban shipped green and still leaked twice: once via the substring
   misclassification, once via the late-appended analysis section that the main
   postprocess never saw.

## External docs and stakeholders

- Team-facing example doc (Action Network examples, operator-agnostic mapping table):
  Google Doc `1H_XLQmwjhDThdpYaqK3fqoLzvI4dW4wbE_sIb5YAu-A`.
- GOAL-facing example doc (for Tom Fuller, GOAL): `1tqDALp-inmM5hpC-HgZ8UawHuglB1z0vZfcxu0YyD30`.
- GOAL brief source of truth: "Ultimate SEO Guide" sheet
  (`1pXgc3fYAQxpUpzgw2sDCeRf1DSZWECvxwvTqjdd_hm0`, tab "Top Stories Hub").
- Content audit driving the current Action Network work: Nick's "Content Audit of Ai
  Tool (Claude)" doc (`1eIBvWI5rpKblu0qX5IZlz5wK6LI27jHDboGuNNHkHw8`).
- Stakeholders: Tom Fuller (GOAL side), Nick (Action Network content audits, biweekly
  feedback loop — the last applied round is 2026-07-21, commit `d7ecbb8`).

## Open threads at handoff

- **Nick's audit, themes 1–4: DONE** (commits `24b4267`..`c01a0ca`, deployed).
  Root-cause fixes for the $15/$10 bonus crossing, the 18+/21+ age contradiction, the
  code-fused-to-next-word glitches, and clock style; deterministic PM worked examples;
  the quick-facts block; the state-list fix; question-mapped headings; and the
  `check_offer_consistency` validator. That validator is the *net* over the root-cause
  fixes: it compares any reward-noun-bound amount and any age token in visible copy
  against the offer's own values, never invents a value to check against (absent fact →
  no check, not a false positive), and is reported on `/validate` but deliberately NOT a
  hard publish gate — Nick did not ask for one, and the root causes are fixed at
  generation, so the net stays quiet.
- **Nick's 2026-07-21 biweekly: DONE** (`d7ecbb8`) — weather points rounded to human
  values (BC Core reports "72.4°, 8.7 mph"; nobody writes weather in decimals — still
  the sourced numbers, see `expertise_context.py:195`), the "% of bets" → "% of
  tickets" noun fix, and the ET-day event window (the Monday-facts-in-Tuesday's-article
  bug).
- **Awaiting Nick's reply** on whether the deterministic (more templated) worked example
  is an acceptable trade against the "first-hand texture" he praised.
- **Theme 5 (E-E-A-T) is ON HOLD** by explicit decision: Nick asks for first-hand
  platform observations the tool cannot truthfully generate (it has never used Kalshi).
  Do not prompt the model for "personal experience" — that is fabrication, the exact
  failure mode months of work removed. The honest version is signal we actually hold:
  settlement mechanics (needs a real data source), liquidity, closing lines.
- **Evergreen/perishable split** (Nick theme 3): partly done — prices only render from
  matched markets now. The structural split (a dated "prices as of" module separate from
  evergreen mechanics) is not built.
- **Player props / closing lines (BC Core)**: recon done, NOT implemented. Props are in
  the `/events/{id}/markets` payload we already fetch (243 markets on a test MLB event)
  but outcomes carry `playerId` with no name resolution — lineups publish near first
  pitch and the two obvious name endpoints returned empty. Closing lines:
  `/{sport}/{leagueId}/events/markets/closing-lines` takes leagueId + start/end (NOT
  eventIds — that 500s); fetch the window and index by eventId.
- **BAM data issues flagged upstream, unresolved**: the per-offer `states` field is junk
  (`source_locations` — the locations BAM actually served the offer for — is truth;
  `_normalize_catalog_offer_states` merges accordingly); and BAM serves Canadian-only
  brands under US location overrides (deliberately left; a brand blocklist would be the
  only fix and nobody wants to maintain one).
