# PlanWrite v2 (TopStoriesGenerator) — Team Handoff

_Last updated: 2026-07-16. Written for the team taking over the project. Everything here is
verified against the code at commit `c01a0ca` unless marked otherwise._

## What this is

An internal Better Collective tool that generates sports-betting promo articles ("top
stories") for BC properties — Action Network (sportsbook, prediction market, DFS offers)
and GOAL.com (its own editorial brief). A writer drives a web wizard; the tool assembles
offer facts, live odds, and event context, generates an outline and draft with an LLM,
runs ~100 deterministic quality passes over the output, and validates the result against
compliance rules. Live at https://topstories-api-production.up.railway.app.

The design philosophy, learned the hard way over months of editorial feedback: **facts are
rendered deterministically from source data; the LLM writes connective prose only.** Every
incident in the git history where a number, state list, price, or age was wrong traces to
the model being allowed to restate a fact. When you extend the tool, keep fact-carriers
(worked examples, terms, signup steps, quick facts, state lists) out of the model's hands.

## Architecture at a glance

FastAPI + Jinja2/HTMX/Alpine (no SPA build). ~19,900 lines under `app/`, dominated by
`app/services/draft.py` (6,750 lines — see Known debt). SQLite by default
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
  │     then: postprocess chain (~18 passes) → disclaimer → switchboard links
  │           → humanizer pass → late strip chain → GOAL footer → tracking tag
  ▼
/api/generate/validate ─► compliance.validate_content (9 checks, scored)
```

Every run persists its stages under `storage/generation_runs/<date>/<run_id>/`
(`00_request`, `10_source_facts`, `20_outline`, `30_draft`, `40_validation`,
`manifest.json`) via `app/services/generation_artifacts.py`. When debugging "why did the
article say X", start from `10_source_facts.json` — it is the single record of every fact
that was fed to prompts.

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
| `draft.py` (6,750) | The draft engine: section generators, deterministic renderers, humanizer, all postprocess passes. |
| `outline.py` (1,824) | LLM structured outline, editorial section rules, question-mapped PM headings, token/text conversions. |
| `expertise_context.py` (1,228) | Lineup/absence/form notes from BC Core for editorial points. |
| `internal_links.py` (912) | Per-property evergreen link indexes (embeddings), suggestion/required/picker APIs. |
| `bam_offers.py` (879) | BAM offer fetcher, pickle caching, state normalization, switchboard URLs. |
| `compliance.py` (785) | All validation checks incl. `check_offer_consistency`, state disclaimers, scoring. |
| `prediction_markets.py` (694) | Kalshi/Polymarket search + example-text builder. |
| `bc_core.py` (630) | BC Core client: operator context, event matching, prompt summaries (via Tailscale SOCKS). |
| `odds_fetcher.py` (555) | Charlotte/RotoGrinders odds client + bet-option/example builders. |
| `offer_parsing.py` (487) | Regex extraction from offer text/terms: amounts, states, exclusions, min age, expiry. |
| `goal_template.py` (328) | GOAL deterministic outline, terms table, sticky CTA + page-blocker footer. |
| `bc_core_odds.py` (308) | BC Core market prices → `/api/odds/game` shape (soccer coverage Charlotte lacks). |
| `event_fetcher.py` (292) | ESPN schedules, featured-game pick. |
| `generation_artifacts.py` (292) | `source_facts` builder + per-run artifact dirs. |
| `rag.py` / `rag_builder.py` | FAISS style-example retrieval (style only, never facts). |
| `llm.py` (206) | AsyncOpenAI wrapper: chat, streaming, JSON mode, embeddings. |
| `usage_tracking.py` (202) | Login/API usage events, summary, CSV export. |
| `switchboard_links.py` (199) | Affiliate tracking link injection. |
| `bc_core_boosts.py` (143) | Live odds boosts (GOAL "promos today" block). |
| `quick_facts.py` (126) | Deterministic quick-facts table (code/trigger/reward/age/event/states). |
| `operator_profile.py` (103) | Content-mode decision (sportsbook / prediction_market / dfs). |
| `operator_facts.py` (100) | Curated overrides where feeds are thin (e.g. Underdog age rules, sport-scoped standing promos). |

### Content modes and properties — two orthogonal axes

- **Content mode** (`operator_profile.py`): operator name → `sportsbook`,
  `prediction_market` (Kalshi, Polymarket, Novig), or `dfs` (Sleeper, Underdog, Dabble).
  Controls language guardrails (PM articles never say bet/odds/sportsbook), deterministic
  PM/DFS renderers, adapted disclaimers, question-mapped headings.
- **Property** (`offer_property` request field): `action_network` (default) vs `goal_com`.
  `is_goal_property` gates a completely different article shape: deterministic outline
  (no LLM outline at all), ≤150-char intro, 3-row terms table, live-odds talking points,
  boosts/promos section, sticky CTA (placement 2066 / property 326) + US page blocker,
  ~550-600 word target. GOAL articles do NOT get the quick-facts table.

## Data sources and migration state

| Source | What it supplies | State |
|---|---|---|
| **BC Core** (`bc_core*.py`, `expertise_context.py`) | Events, lineups/absences/form ("expertise" editorial points), operator context, odds boosts, market prices incl. soccer 1X2 | Primary and only source for everything except US odds |
| **Charlotte / RotoGrinders** (`odds_fetcher.py`) | US sportsbook odds for the editor's bet builder | Still primary for US odds; BC Core is the fallback |
| **BAM** (`bam_offers.py`) | Offers, codes, terms, states, switchboard links | Primary, pickle-cached 6h, schema v3 |
| **Polymarket/Kalshi** (`prediction_markets.py`) | Prediction-market search + real contract prices | On-demand, in-memory cache |
| **ESPN** (`event_fetcher.py`) | Game schedules for the UI picker | Plain HTTP; soccer hardcoded to `fifa.world` |
| **OpenAI** (`llm.py`) | Drafting + embeddings (`gpt-5.5-2026-04-23`) | Only provider; model-prefix shims in `llm.py:33` |

**Why Charlotte is not retired (a hard constraint, not laziness):** BC Core's
`/sportsbooks` has **no DraftKings** (verified against all 472 books). Charlotte covers
DraftKings; BC Core adds FanDuel and soccer, which Charlotte lacks entirely (every soccer
fixture returns null there). BC Core moneyline/total parity vs Charlotte was verified
live (exact match, four books). So the odds path is: Charlotte first, BC Core fallback
(`app/api/odds.py:64-82` game-not-found, `:127-138` no-board), and `bc_core_odds.py:123`
maps BC Core markets into the identical response shape so a future swap is mechanical.

**Half-migrated edges the next team should know:**
- **Sport-key mismatch, latent bug**: boosts are keyed `ncaafb`/`ncaamb`
  (`bc_core_boosts.py:23-31`) while the app's sport codes elsewhere are `ncaaf`/`ncaab` —
  a boost lookup for college football returns nothing, silently.
- **Dual transport remnant** in `bc_core.py:15-16`: public host + legacy proxy host both
  configured; the Tailscale SOCKS path is what production actually uses.
- Stale hardcoded default week `"2025-reg-13"` in `odds_fetcher.py:74,152`.
- **ESPN soccer is World-Cup-only** (`event_fetcher.py:20` hardcodes `soccer/fifa.world`)
  — the soccer picker goes dark after the tournament. BC Core `/{sport}/events` is the
  natural replacement and is already used for event matching.
- **Player props / closing lines**: recon complete, unimplemented (see Open threads).

## Known debt and jank

An honest inventory, tiered by how much it will hurt you.

### Structural (will hurt immediately when you start changing things)

- **`draft.py` is a god-module**: 6,750 lines, 218 functions — prompts, ~80 regex
  postprocess passes, renderers, humanizer, markdown conversion, and both orchestrators
  in one file.
- **The two orchestrators are ~95% copy-pasted** (`generate_draft_from_outline`
  `draft.py:4867` vs `..._streaming` `:6292`, ~290 lines each; same again in
  `app/api/generate.py` where the offer-fetch/source-facts/enrich block repeats four
  times). Every pipeline fix must be applied twice, and they have already drifted:
  the `is_terms` heading list differs between paths (`draft.py:5809` includes "house
  rules/market rules/settlement"; `:6617` does not), and **the streaming paths write no
  generation artifacts** — artifact coverage depends on which endpoint the UI hit.
- **Section routing is English-string matching** (`_is_claim_heading`,
  `_is_signup_heading`, `is_terms` substrings, plus a parallel set in `outline.py`).
  The outline computes `section_kind` and throws it away; plumbing it through is the fix.
- **Single-replica by construction**: SQLite, module-level caches
  (`bam_offers`, `prediction_markets`, `bc_core_odds` — the latter with no TTL at all),
  and filesystem artifact reads (`list_same_day_run_titles`, `/api/generate/runs/{id}`)
  all assume one process on one disk. Scaling out requires Postgres + shared cache +
  object storage first.

### Latent bugs found in audit (not yet fixed)

- `ncaaf`/`ncaafb` boost key mismatch (above) — college boosts silently empty.
- Soccer schedule picker dies after the World Cup (above).
- `_html_to_markdown` (`draft.py:6270`) parses HTML with regex; handles `<ol>` but not
  `<ul>`, and attributes on `<p>` corrupt output.
- Legacy token pipeline (`generate_draft` `:6679`, `generate_draft_streaming` `:6719`,
  `parse_token` `:6581`) has **zero callers** — delete on sight, but note the is_terms
  divergence lives partly here.
- Dead code: `GOAL_TARGET_WORDS` (`goal_template.py:18`, never read),
  `render_bam_offer_block` (imported, never called), `build_bet_options` (same),
  `inject_brand_links` (no callers), `app/workers/` (empty package).

### Operational hygiene

- **Nothing cleans `storage/`** — zero cleanup code in the app. 97 pickles across three
  cache-schema generations (only v3 is read), 128 ad-hoc QA HTML files in
  `manual_checks/`, and `generation_runs/` grows one directory per run, forever.
- **62 broad `except Exception` across 22 files**, many returning `{}`/`[]` silently — a
  BC Core outage degrades articles (no odds, no boosts) with nothing in the logs saying
  why. When output looks mysteriously thin, suspect a swallowed upstream failure first.
- `datetime.utcnow()` (deprecated) in `bam_offers.py` and `usage_tracking.py`; the rest
  of the codebase uses `datetime.now(UTC)`. `print()` instead of structlog in
  `odds_fetcher`, `event_fetcher`, `bam_offers`.
- Pickle as the BAM cache format — schema-brittle (the v2→v3 scar) and an
  arbitrary-code-execution risk if storage were ever shared.
- `uv.lock` is **untracked** while `pyproject.toml` is tracked — builds are not
  reproducible until it's committed.
- Repo-root clutter: ~15 untracked one-off manifest/compare scripts from a data-sync
  exercise, a stale `.railway-deploy-current/` source snapshot, and ~50 stale deploy
  worktrees under `%TEMP%` on the dev machine. None of it is load-bearing except
  `docs/charlotte-odds-api.md` and `scripts/create_bc_brief_doc.py` (which embeds a
  personal-machine token path) — both untracked.
- Easy performance wins left on the table: sequential awaits in
  `app/api/generate.py:684-685`, and `get_offer_by_id_bam` re-fetches the whole catalog
  per alt-offer id.

### Where the quality actually lives

The editorial/output layer is the part months of feedback hardened: deterministic
fact-carriers, the postprocess chains, the 329 tests, the compliance checks. Trust it.
The infrastructure layer is a solo-built internal tool that has never needed to scale —
fine at one replica and a handful of writers, but it is where a new team will feel the
friction first.

## Operational runbook

### Local dev

- Local venv is **Python 3.13**; production image is **python:3.11-slim**. This skew has
  caused a production outage once: PEP 701 f-strings (nested same-quote) parse on 3.13,
  pass all tests, and crash the app on import in 3.11 — a 502 after a SUCCESS deploy.
- Tests: `.\.venv\Scripts\python.exe -m pytest` (NOT global python). 329 passing at
  `c01a0ca`.
- **Before every deploy**: `python scripts/check_py311_grammar.py` — parses all of
  `app/**/*.py` with `feature_version=(3,11)`. Non-negotiable given the skew above.

### Deploy (Railway)

Project `planwrite-v2-topstories` (`b722d2ce-2e5b-4529-a4c4-14963ec85eab`), environment
`production` (`53e36246-d009-485a-b585-2e3aa2a5eabf`), service `topstories-api`
(`5152a3d3-dd85-4b64-b026-133c9c9dfe32`). Login creds live in Railway vars
(`AUTH_USERNAME`/`AUTH_PASSWORD`; fetch via `railway variables --json` from a linked dir).

Trusted procedure (deploys exactly what is committed, never the dirty working tree):

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
`TS_AUTHKEY`/`TS_HOSTNAME` vars; see `docs/hetzner-tailscale-tunnel.md`).

### LLM (gpt-5.5) gotchas, burned in production

- Model: `gpt-5.5-2026-04-23` (Railway var + `LLM_MODEL` default). It **rejects
  non-default temperature** — `_sampling_params` omits temperature and sends
  `reasoning_effort="low"` (valid: none/low/medium/high/xhigh; "minimal" is NOT valid).
- **Reasoning tokens come out of `max_completion_tokens`** — keep the +1500 headroom or
  intros truncate mid-sentence and the deterministic fallback ships visibly broken copy.

### Editorial conventions that look like bugs but are deliberate

- Availability is prose, never a "States Available:" label. Nationwide-with-exclusions
  renders as "all US states except …" — never an enumeration (the model truncates lists
  it recites; see `_collapse_state_enumerations`).
- Worked examples must NOT show total payout figures for sportsbook articles (tests
  assert this); profit-only figures are OK. Prediction-market examples only print a
  contract price when a real matched market supplied it.
- bet365 is always lowercase in copy. Branded reward currencies ("Novig Coins") keep
  casing; generic shouty labels get lowercased mid-sentence except in headings/terms.
- Primary keyword density target 5–9 mentions; overage converts to brand-only text
  (`_cap_primary_keyword_density`). There is deliberately NO keyword-filler machinery —
  it was removed; do not reintroduce padding to hit density.
- Headings h1–h3 are title-cased in postprocess, then brand casing restored.
- The article lede has ONE canonical shape (see `LEDE TEMPLATE` in the intro prompt). Do
  not rotate opener styles; only the facts inside the shape vary.

### Engineering lessons this codebase keeps re-teaching

Written down because each has caused at least one production defect:

1. **Models paraphrase around ban lists.** A prompt rule alone has never held. Every ban
   needs a deterministic regex backstop in the postprocess chain, and every fact the
   model keeps mangling eventually needs a deterministic renderer instead.
2. **Inline tags split text nodes.** `<strong>` in the middle of a sentence means your
   sentence-level regex sees two fragments. This class of bug has shipped three times
   ("ACTIONdeposit"). `_rewrite_html_text_nodes(..., with_block_context=True)` exists so
   transforms can tell a real block start from a mid-sentence node — use it.
3. **Word boundaries in classifiers.** "out" matched "pitching outs" once and
   misclassified a projection as injury news, publishing a banned number.
4. **Section routing keys off heading text.** `_is_claim_heading`, `_is_signup_heading`,
   and the `is_terms` substring check route sections to their deterministic renderers by
   matching English. Changing a heading can silently downgrade a section to generic LLM
   prose. The outline already computes `section_kind` and throws it away — plumbing it
   through to `_generate_body_section` is the right fix and has not been done yet.
5. **Verify category filters against live output**, not just unit fixtures. The
   projection ban shipped with a passing test suite and still leaked via a category
   misclassification.

## External docs and stakeholders

- Team-facing example doc (Action Network examples, operator-agnostic mapping table):
  Google Doc `1H_XLQmwjhDThdpYaqK3fqoLzvI4dW4wbE_sIb5YAu-A`.
- GOAL-facing example doc (for Tom Fuller, GOAL): `1tqDALp-inmM5hpC-HgZ8UawHuglB1z0vZfcxu0YyD30`.
- GOAL brief source of truth: "Ultimate SEO Guide" sheet
  (`1pXgc3fYAQxpUpzgw2sDCeRf1DSZWECvxwvTqjdd_hm0`, tab "Top Stories Hub").
- Content audit driving the current Action Network work: Nick's "Content Audit of Ai
  Tool (Claude)" doc (`1eIBvWI5rpKblu0qX5IZlz5wK6LI27jHDboGuNNHkHw8`).
- Stakeholders: Tom Fuller (GOAL side), Nick (Action Network content audits).

## Open threads at handoff

- **Nick's audit, themes 1–4: DONE** (commits `24b4267`..`c01a0ca`, deployed). Root-cause
  fixes for the $15/$10 bonus crossing, 18+/21+ age contradiction, code-fused-to-next-word
  glitches, clock style; deterministic PM worked examples; quick-facts block; state-list
  fix; question-mapped headings; `check_offer_consistency` validator (reported on
  `/validate`, deliberately not a hard publish gate — Nick did not ask for one).
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
  pitch and the two obvious endpoints returned empty. Closing lines:
  `/{sport}/{leagueId}/events/markets/closing-lines` takes leagueId + start/end (NOT
  eventIds — that 500s); fetch the window and index by eventId.
- **BAM data issues flagged upstream, unresolved**: per-offer `states` field is junk
  (`source_locations` is truth — `_normalize_catalog_offer_states` handles it); BAM
  serves Canadian-only brands under US location overrides (deliberately left; a brand
  blocklist would be the only fix).
