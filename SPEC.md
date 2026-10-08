# Victor AI & Tech — Product Spec (free edition)

Rebuilt from the master prompt after analysis. Goal unchanged: original, sourced AI/tech posts go out on X around the clock, including while the operator sleeps, at zero monthly cost. Scope cut to what the goal actually needs.

## 1. What it does

Every 30 minutes a job runs and does the following, in order:

1. **Collect** new items from a watch list of free sources (RSS/Atom feeds, Google News topic feeds, Hacker News, Hugging Face trending, Reddit feeds, YouTube official channels). Conditional GET, per-source timeouts, one failing source never stops the others.
2. **Normalize**: canonical URL (strip tracking params), clean title, publisher, published time, discovered time, language, raw text.
3. **Filter** cheaply, no AI: off-topic keywords, non-English, older than 36 h, sponsored, already seen.
4. **Cluster** items into stories: same canonical URL, title similarity, shared entities, time window. Twelve articles about one launch become one story with twelve sources.
5. **Score** each story from counts: independent sources, arrival velocity, presence of a Tier 1 (official) source, novelty vs. last 7 days of posts, topic weight. Classify: breaking / hot / trending / developing / ignore. Thresholds live in config.
6. **Find media** that is permitted: an official X post URL inside the source text (to quote), a YouTube video from an official channel, public-domain media (NASA). Rights status recorded. Otherwise mark "render own clip" (later phase).
7. **Draft** the top N stories with the AI provider: voice guide + example posts + the story's source texts. Output: post text under 280 chars, a why-it-matters line, a one-line reason the story was chosen, and the source IDs used. Deterministic checks afterwards: length, links resolve, every URL in the draft exists in the sources, nothing similar posted in 7 days.
8. **Route**: if the overnight rule passes and we are inside the autonomous window and under the nightly cap, publish. Otherwise send the draft to Telegram with Approve / Edit / Reject buttons and wait.
9. **Publish** approved drafts via the X adapter at the next slot that respects the posting limits. Store the returned post ID and URL. On a timeout after submit, reconcile before any retry.
10. **Log** every post with its sources and reason. Record every run, every failure, every rejection.

## 2. Control surface

- **Telegram** (only the configured user id): draft cards with Approve / Approve for night / Edit / Reject / Rewrite. Commands: /pause /resume /status /tonight /posted /block /boost /night /mode /note.
- **`config/settings.yaml`**: overnight rule, posting limits, content mix, thresholds, automation mode.
- **`config/sources.yaml`**: watch list with tier, category, interval, enabled.
- **`config/voice.md`**: the voice guide and example posts read before every draft.
- Later: a free read-only dashboard on Vercel reading the same database.

## 3. Overnight rule (defaults, operator-owned)

A story may publish with no human only when all hold:
- automation mode is `restricted_autonomous` and not paused
- current time is inside the autonomous window (default 23:00–07:00 local)
- story has at least one Tier 1 source and at least 2 other independent sources
- category is in the allowed list (model releases, product launches, research, space missions); never politics, security incidents, layoffs, lawsuits, allegations, rumours
- all draft checks passed
- fewer than `night_cap` (default 2) autonomous posts since the window opened
- posting limits (per day, per hour, min gap) are respected

## 4. Posting limits (defaults)
max 6 posts/day, max 2 posts/hour, min 45 min gap, quiet hours none (overnight is allowed by design), duplicate cooldown 7 days per story and 3 days per primary entity.

## 5. Content mix (defaults, advisory not quota)
AI news and model releases 35, AI tools and practical 30, tech and research 15, space and science 10, analysis 10. Educational posts are drafted only from operator notes (`/note`), never invented.

## 6. Media policy
Quote the official X post when its URL is present in a source. Else attach public-domain or press-kit media whose rights are recorded. Else render an original clip (later phase). Never republish third-party clips. Source link goes in the first reply, not the post.

## 7. Data model
sources, items, stories, story_items, drafts, posts, notes, runs, events. SQLAlchemy models in `victor/models.py` are the reference.

## 8. Phases
- **Phase 1 (this build)**: collect → normalize → filter → cluster → score → media extract → draft (mock or Gemini/Groq) → route to console/Telegram → mock publish. CLI. Tests. GitHub Actions workflow. Runs with zero keys.
- **Phase 2**: real Telegram bot with buttons and commands; approval state machine; real Gemini drafting.
- **Phase 3**: X publisher with the real API, reconciliation, limits enforced live; nightly autonomous window on.
- **Phase 4**: media: YouTube official channels, NASA, quote-post detection hardened, Remotion clip rendering.
- **Phase 5**: operator notes → educational posts; manual growth numbers; weekly digest to Telegram.
- **Phase 6**: read-only dashboard on Vercel (free).

## 9. Out of scope until the account justifies it
X read access, trend detection from X, source discovery via search APIs, multi-user auth, learning engine, full three-column dashboard, blog publishing.
