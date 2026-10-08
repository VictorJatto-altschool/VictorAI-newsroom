"""Static read-only dashboard, written to docs/index.html and served free by GitHub Pages.

No server, no JavaScript framework. It is a snapshot of the database at the end of each run.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import ROOT, Settings
from .models import Draft, GrowthSnapshot, Item, Post, Run, Source, Story

DOCS_DIR = ROOT / "docs"
CSS = """
:root{--bg:#000;--bg2:#0A0A0A;--card:#111;--b:#2F3336;--t:#FFF;--m:#71767B;--a:#1D9BF0;--ok:#00BA7C;--warn:#FFD400;--bad:#F4212E}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:16px 20px;border-bottom:1px solid var(--b);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:18px;margin:0}h2{font-size:15px;margin:0 0 10px;color:var(--m);text-transform:uppercase;letter-spacing:.04em}
.muted{color:var(--m)}.wrap{max-width:1180px;margin:0 auto;padding:16px 20px;display:grid;gap:16px;grid-template-columns:1fr}
@media(min-width:900px){.wrap{grid-template-columns:2fr 1fr}}
.card{background:var(--card);border:1px solid var(--b);border-radius:12px;padding:14px 16px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.stat b{display:block;font-size:22px}.stat span{color:var(--m);font-size:12px}
.row{padding:10px 0;border-top:1px solid var(--b)}.row:first-of-type{border-top:0}
.pill{display:inline-block;font-size:11px;padding:1px 8px;border-radius:999px;border:1px solid var(--b);color:var(--m);margin-right:6px}
.pill.breaking{color:var(--bad);border-color:var(--bad)}.pill.hot{color:var(--warn);border-color:var(--warn)}.pill.trending{color:var(--a);border-color:var(--a)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--ok)}.dot.bad{background:var(--bad)}.dot.warn{background:var(--warn)}
a{color:var(--a);text-decoration:none}a:hover{text-decoration:underline}
.dev{background:#2a2300;color:var(--warn);border:1px solid var(--warn);padding:6px 10px;border-radius:8px;font-size:12px}
pre{white-space:pre-wrap;font:inherit;margin:6px 0 0}
"""


def _aware(dt):
    return dt if (dt is None or dt.tzinfo) else dt.replace(tzinfo=timezone.utc)


def _fmt(dt, tz) -> str:
    return _aware(dt).astimezone(tz).strftime("%d %b %H:%M") if dt else "—"


def build_html(s: Session, settings: Settings, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.automation.get("timezone", "UTC"))
    cfg = settings.raw.get("dashboard", {})
    show_drafts = bool(cfg.get("show_drafts", False))
    e = html.escape

    last = s.scalars(select(Run).order_by(Run.id.desc()).limit(1)).first()
    day = now - timedelta(days=1)
    stats = {
        "items 24h": s.scalar(select(func.count(Item.id)).where(Item.discovered_at >= day)),
        "stories 24h": s.scalar(select(func.count(Story.id)).where(Story.first_seen_at >= day)),
        "pending": s.scalar(select(func.count(Draft.id)).where(Draft.status == "pending")),
        "queued": s.scalar(select(func.count(Draft.id)).where(Draft.status.in_(("approved", "approved_night")))),
        "posted 7d": s.scalar(select(func.count(Post.id)).where(Post.created_at >= now - timedelta(days=7),
                                                               Post.status.in_(("published", "mock")))),
        "failed": s.scalar(select(func.count(Post.id)).where(Post.status.in_(("failed", "uncertain")))),
    }
    stories = s.scalars(select(Story).where(Story.last_updated_at >= now - timedelta(days=2))
                        .order_by(Story.score.desc()).limit(25)).all()
    posts = s.scalars(select(Post).order_by(Post.id.desc()).limit(15)).all()
    sources = s.scalars(select(Source).order_by(Source.tier, Source.name)).all()
    growth = s.scalars(select(GrowthSnapshot).order_by(GrowthSnapshot.at.desc()).limit(1)).first()
    pending = s.scalars(select(Draft).where(Draft.status == "pending").order_by(Draft.id.desc()).limit(10)).all()

    def story_row(st: Story) -> str:
        src = f"{st.source_count} src" + (f", {st.tier1_count} official" if st.tier1_count else "")
        return (f'<div class="row"><span class="pill {e(st.classification)}">{e(st.classification)} {st.score:.0f}</span>'
                f'<span class="pill">{e(st.category)}</span><span class="pill">{e(st.status)}</span> {e(st.title)}'
                f'<div class="muted" style="font-size:12px">{src} · updated {_fmt(st.last_updated_at, tz)}</div></div>')

    def post_row(p: Post) -> str:
        link = f'<a href="{e(p.remote_url)}">{e(p.remote_url)}</a>' if p.remote_url else ""
        cls = "" if p.status == "published" else ("warn" if p.status == "mock" else "bad")
        return (f'<div class="row"><span class="dot {cls}"></span><b>{e(p.status)}</b>{" · overnight" if p.autonomous else ""} '
                f'<span class="muted">{_fmt(p.created_at, tz)}</span> {link}<pre>{e(p.text[:220])}</pre></div>')

    def src_row(r: Source) -> str:
        if r.consecutive_failures >= 3:
            cls, label = "bad", f"failing x{r.consecutive_failures}"
        elif r.consecutive_failures:
            cls, label = "warn", f"retrying x{r.consecutive_failures}"
        else:
            cls, label = "", f"ok · {_fmt(r.last_success_at, tz)}"
        return f'<div class="row" style="padding:6px 0"><span class="dot {cls}"></span>t{r.tier} {e(r.name)} <span class="muted" style="font-size:12px">{e(label)}</span></div>'

    run_line = (f"last run {_fmt(last.started_at, tz)} · {'ok' if last.ok else 'FAILED'}" if last else "no runs yet")
    dev = '<span class="dev">DEV MODE · mock adapters · nothing was posted</span>' if settings.env.dev_mode else ""
    g = settings.raw.get("growth", {})
    growth_html = ("<div class='muted'>no numbers recorded yet</div>" if not growth else
                   f"<div class='stats'><div class='stat'><b>{growth.followers}</b><span>followers / {g.get('follower_target', 500)}</span></div>"
                   f"<div class='stat'><b>{(growth.verified_impressions_90d or 0):,}</b><span>verified impressions 90d / {int(g.get('impressions_target_90d', 500000)):,}</span></div></div>"
                   f"<div class='muted' style='font-size:12px'>manual entry {_fmt(growth.at, tz)} · check X's current program rules</div>")
    drafts_html = ""
    if show_drafts:
        drafts_html = "<div class='card'><h2>Pending drafts</h2>" + ("".join(
            f'<div class="row"><b>#{d.id}</b> <span class="muted">{e(d.story.title[:80])}</span><pre>{e(d.text)}</pre></div>' for d in pending)
            or "<div class='muted'>none</div>") + "</div>"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Victor AI &amp; Tech — newsroom</title><style>{CSS}</style></head><body>
<header><h1>Victor AI &amp; Tech · newsroom</h1><span class="muted">{e(run_line)} · generated {_fmt(now, tz)} {tz.key}</span>{dev}</header>
<div class="wrap">
<div>
<div class="card"><div class="stats">{''.join(f'<div class="stat"><b>{v}</b><span>{e(k)}</span></div>' for k, v in stats.items())}</div></div>
<div class="card" style="margin-top:16px"><h2>Stories, last 48h</h2>{''.join(story_row(x) for x in stories) or '<div class="muted">nothing yet</div>'}</div>
<div class="card" style="margin-top:16px"><h2>Posts</h2>{''.join(post_row(x) for x in posts) or '<div class="muted">nothing posted yet</div>'}</div>
</div>
<div>
<div class="card"><h2>Growth</h2>{growth_html}</div>
<div class="card" style="margin-top:16px"><h2>Sources</h2>{''.join(src_row(x) for x in sources)}</div>
{drafts_html}
</div></div></body></html>"""


def write_dashboard(s: Session, settings: Settings, now: datetime | None = None, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or DOCS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "index.html"
    p.write_text(build_html(s, settings, now), encoding="utf-8")
    (out_dir / ".nojekyll").touch()
    return p
