# Victor AI & Tech — Newsroom

A scheduled job that watches free AI/tech sources worldwide, groups what it finds into stories, scores them, drafts original posts in the account's voice, routes them to you for approval (or posts on its own under a strict overnight rule), and publishes to X. Zero monthly cost. See `SPEC.md` for the design and `CLAUDE.md` for the rules every change must respect.

**Status: Phase 1.** Collect → filter → cluster → score → media → draft → route → publish all run end to end. With no API keys the AI, Telegram and X adapters are mocks and every output is labelled `[DEV MODE]`. Nothing is posted anywhere until real keys exist and the X adapter is switched on in Phase 3.

## Run it locally

```bash
pip install -e ".[dev]"
```

```bash
python -m victor run
```

That fetches the real feeds in `config/sources.yaml`, stores everything in `data/victor.db`, and prints draft cards to the console. Then:

| Command | What it does |
|---|---|
| `python -m victor status` | last run, counts, failing sources |
| `python -m victor stories` | top stories by score |
| `python -m victor drafts` | drafts and their checks |
| `python -m victor approve 12` | approve draft 12 for the next slot |
| `python -m victor night 12` | approve draft 12 for the overnight window only |
| `python -m victor reject 12` | reject it |
| `python -m victor pause` / `resume` | emergency stop for all publishing |
| `python -m victor mode approval` | `manual`, `approval` or `restricted_autonomous` |
| `python -m victor note "tried X, it does Y"` | save a tool note for future educational posts |
| `python -m victor posted` / `events` | what went out, full audit trail |

Tests: `pytest -q`.

## Configure it

- `config/settings.yaml` — overnight rule, posting limits, scoring weights, content mix, filter. Enforced in code.
- `config/sources.yaml` — watch list with tier, category, interval and official X handle.
- `config/voice.md` — the voice guide and example posts the model reads before every draft.
- `.env` (copy from `.env.example`) — keys. Each missing key switches that adapter to its mock.

## Run it 24/7 for free

1. Push this folder to a **public** GitHub repo (unlimited Actions minutes).
2. Add the secrets from `.env.example` under Settings → Secrets → Actions. Only the ones you have.
3. Add `DATABASE_URL` pointing at a free Supabase or Neon Postgres so state survives between runs. Without it the workflow keeps `data/victor.db` in the Actions cache, which works but can be evicted.
4. The workflow in `.github/workflows/newsroom.yml` runs every 30 minutes. Run it once by hand from the Actions tab to check.

## How a story becomes a post

1. Feeds are fetched with conditional GET; a failing source backs off and never stops the others.
2. Items are normalized (canonical URL, clean title, entities) and cheaply filtered (old, off-topic, sponsored, non-English).
3. Items that describe the same event are clustered into one story (title similarity + shared entities + 48 h window). Google News copies of an official post count as the same publisher.
4. Stories are scored from counts only: independent sources, arrival speed, official source present, novelty vs. recent posts, topic weight. One source never reaches "trending" unless it is an official announcement under 3 hours old.
5. The top 3 stories per run are drafted by the AI provider from their own source texts, then checked deterministically: length, no URLs or hashtags, every number appears in a source, no banned hype words, source link resolves.
6. Media is chosen by rights: quote an official X post found in the sources, else attach public-domain media, else mark for an original rendered clip (Phase 4).
7. The overnight rule decides the route. Everything goes to review unless mode is `restricted_autonomous`, it is inside the window, the story has an official source plus two others, the category is allowed, no sensitive keyword appears, the night cap and posting limits allow it.
8. Approved drafts are published at the next slot that respects the limits. Each draft version gets one idempotency key, so a retry can never double-post. A timeout after submit is recorded as `uncertain` and never retried automatically.

## Known limitations and blockers

- **X posting is wired but off.** Confirm the current X API free-tier terms (write access, media upload, quote posts) before adding the X keys. Without them the publisher is a mock.
- **No real AI until a key exists.** Gemini (free tier) is first in `provider_order`, then Groq, then the template mock.
- **Telegram sends cards but does not yet read button presses.** Phase 2 adds the command loop. Until then approve with the CLI.
- **Sources with no RSS** (Anthropic, Mistral, xAI, SpaceX) are covered through Google News queries. Reddit and some publishers rate-limit; the backoff handles it.
- **Educational posts** are not generated yet. They will draft only from `note` entries, never from nothing.
- **Growth numbers** (followers, verified impressions) are not readable on the free X tier and will be entered by hand in Phase 5.
