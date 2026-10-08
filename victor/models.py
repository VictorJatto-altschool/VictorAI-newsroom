"""Persistent records. Every draft and post keeps its lineage back to source items."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    boost_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    items: Mapped[list["Item"]] = relationship(back_populates="source")


class Item(Base):
    """One collected piece of content, normalized."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("canonical_url", name="uq_items_canonical_url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    source: Mapped[Source] = relationship(back_populates="items")
    external_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    canonical_url: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(200), default="")
    author: Mapped[str] = mapped_column(String(200), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    entities: Mapped[list] = mapped_column(JSON, default=list)
    media: Mapped[list] = mapped_column(JSON, default=list)  # [{type, url, rights, provider}]
    filtered_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id"), nullable=True)
    story: Mapped["Story | None"] = relationship(back_populates="items")


class Story(Base):
    """A cluster of items about one underlying event."""

    __tablename__ = "stories"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="other")
    entities: Mapped[list] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    tier1_count: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    classification: Mapped[str] = mapped_column(String(20), default="ignore")
    status: Mapped[str] = mapped_column(String(20), default="discovered")  # discovered|drafted|posted|skipped
    items: Mapped[list[Item]] = relationship(back_populates="story")
    drafts: Mapped[list["Draft"]] = relationship(back_populates="story")


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id"))
    story: Mapped[Story] = relationship(back_populates="drafts")
    text: Mapped[str] = mapped_column(Text)
    reply_text: Mapped[str] = mapped_column(Text, default="")  # first reply: source link
    why: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")  # why the system chose this story
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80), default="")
    source_item_ids: Mapped[list] = mapped_column(JSON, default=list)
    media: Mapped[dict] = mapped_column(JSON, default=dict)  # {mode: quote|upload|render|none, url, rights}
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    checks_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending|approved|approved_night|rejected|published|failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    route: Mapped[str] = mapped_column(String(20), default="review")  # review|autonomous
    version: Mapped[int] = mapped_column(Integer, default=1)
    dev_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)  # telegram message id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posts: Mapped[list["Post"]] = relationship(back_populates="draft")


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"))
    draft: Mapped[Draft] = relationship(back_populates="posts")
    story_id: Mapped[int] = mapped_column(ForeignKey("stories.id"))
    platform: Mapped[str] = mapped_column(String(20), default="x")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    remote_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remote_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_remote_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # queued|submitted|published|uncertain|failed|mock
    status: Mapped[str] = mapped_column(String(20), default="queued")
    autonomous: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Note(Base):
    """Operator notes: the only legal raw material for educational posts."""

    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Event(Base):
    """Audit trail: every decision the system or the operator makes."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(40))
    ref_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class State(Base):
    """Key/value runtime state: paused flag, mode override, telegram offset."""

    __tablename__ = "state"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
