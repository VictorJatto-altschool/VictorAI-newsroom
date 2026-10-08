# Victor AI & Tech — Newsroom

A scheduled job that watches free AI/tech sources worldwide, groups what it finds into stories, scores them, drafts original posts in the account's voice, routes them to your phone for approval (or posts on its own under a strict overnight rule), renders its own media, and publishes to X. Zero monthly cost. See `SPEC.md` for the design and `CLAUDE.md` for the rules every change must respect.

**Status: all six phases built.** With no API keys the AI, Telegram and X adapters are mocks and every output is labelled `[DEV MODE]`. Nothing is posted anywhere until real keys exist **and** `publishing.enabled` is set to true in `config/settings.yaml`.

| Phase | What it does | State |
|---|---|---|
| 1 | Collect → filter → cluster → score → draft → route → publish | done |
| 2 | Telegram buttons and commands, draft versions, edits | done, needs a bot token to use live |
| 3 | X publisher with media upload, idempotency, reconciliation | done, off until X terms confirmed |
| 4 | Media: quote official posts, public-domain images, own card + clip | done |
| 5 | Educational posts from your notes, growth numbers, weekly digest | done |
| 6 | Read-only dashboard on GitHub Pages | done |

## Run it locally

```bash
pip install -e ".[dev]"
```

```bash
python -m victor run
```

That fetches the real feeds in `config/sources.yaml`, stores everything in `data/victor.db`, prints draft cards to the console and writes `docs/index.html`. Then:

| Command | What it does |
|---|---|
| `python -m victor status` | last run, counts, failing sources |
| `python -m victor stories` / `drafts` / `posted` / `events` | inspect the database |
| `python -m victor approve 12` / `night 12` / `reject 12` | decide a draft from the terminal |
| `python -m victor render 12` | render the card and 8-second clip for draft 12 into `data/media/` |
| `python -m victor note "tried X, it does Y"` | save a note; the next run drafts one educational post from it |
| `python -m victor pause` / `resume` | emergency stop for all publishing |
| `python -m victor mode approval` | `manual`, `approval` or `restricted_autonomous` |
| `python -m victor telegram` | long-poll Telegram locally so buttons and commands work while testing |
| `python -m victor test-ai` | draft one sample post with the configured AI provider |
| `python -m victor test-x` | check the X credentials and show whether publishing is enabled |
| `python -m victor dashboard` | rewrite `docs/index.html` |
| `python -m victor story <link> --note "angle"` | draft a story you found yourself; an X post link is quoted so its video plays |
| `python -m victor serve` | dashboard at http://localhost:8787/ |
| `python -m victor loop --minutes 20` | run cycles continuously while the laptop is on, Telegram polled in between |

Tests: `pytest -q` (45 tests, no network).

## Control it from your phone

Once `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, every draft arrives as a card with **Approve**, **Approve for night**, **Rewrite** and **Reject** buttons. Reply to a card with new text to edit it. Commands: `/status /pending /tonight /posted /pause /resume /mode /night /block /unblock /boost /sources /note /growth /digest /help`. Only your chat id is obeyed. The bot replies to buttons during each scheduled run and continuously when `python -m victor telegram` is running.

## Configure it

- `config/settings.yaml` — automation mode, overnight rule, posting limits, scoring, content mix, filter, publishing switch, growth targets, educational cap, digest time, dashboard options. All enforced in code.
- `config/sources.yaml` — watch list: official blogs, 12 official YouTube channels, press, Google News queries, Hacker News, Hugging Face trending, Reddit. Each has a tier, category, interval and official X handle (only those handles are quote-posted automatically).
- `config/voice.md` — the voice guide and example posts the model reads before every draft.
- `.env` (copy from `.env.example`) — keys. Each missing key switches that adapter to its mock.

## Run it 24/7 for free

1. Push this folder to a **public** GitHub repo (unlimited Actions minutes). Branch `main`.
2. Settings → Secrets → Actions: add the values from `.env.example` that you have.
3. Add `DATABASE_URL` from a free Supabase or Neon Postgres so state survives between runs. Without it the workflow keeps `data/victor.db` in the Actions cache, which works but can be evicted.
4. Settings → Pages: source "Deploy from a branch", branch `main`, folder `/docs`. The dashboard then lives at `https://<you>.github.io/<repo>/`.
5. The workflow runs every 30 minutes and on manual dispatch. Tests run on every push.

## How a story becomes a post

1. Feeds are fetched with conditional GET; a failing source backs off and never stops the others.
2. Items are normalized and cheaply filtered (old, off-topic, sponsored, non-English).
3. Items about the same event are clustered into one story. Google News copies of an official post count as the same publisher.
4. Stories are scored from counts only. One source never reaches "trending" unless it is an official announcement under 3 hours old.
5. The top 3 stories per run are drafted from their own source texts, then checked: length, no URLs or hashtags, every number appears in a source, no hype words, source link resolves.
6. Media by rights: quote an official X post found in the sources; else attach a public-domain image (NASA's library is searched automatically for space stories); else render an original 1080×1080 card and an 8-second clip with ffmpeg. Official YouTube channels are watched through their free RSS feeds; when a story has an official video, its link goes in the first reply under the source link, because external links in the post itself reduce reach on X.
7. The overnight rule decides the route. Autonomous only in `restricted_autonomous` mode, inside the window, with an official source plus two others, an allowed category, no sensitive keyword, under the night cap and the posting limits. Everything else waits for you.
8. Approved drafts publish at the next slot that respects the limits. One idempotency key per draft version, so a retry can never double-post. A timeout after submit is `uncertain`, reconciled against your timeline when the API tier allows, never resubmitted blindly.
9. Educational posts come only from your `/note` entries, at most one per day, always reviewed by you.

## Known limitations and blockers

- **X free-tier terms must be confirmed** before `publishing.enabled: true`. Media upload and the reconciliation lookup may not be available on every tier; the publisher degrades to text-only and `uncertain` respectively.
- **No real AI until a key exists.** Gemini (free tier) first, then Groq, then the template mock.
- **Reading X is not possible** on the free tier, so growth numbers are typed in with `/growth`, and trend signal comes from feeds, not from X.
- **The dashboard is public** when served from a public repo. Draft text is excluded unless `dashboard.show_drafts` is true.
- **Scheduled runs can be late** by several minutes on GitHub Actions. Fine for this use.
