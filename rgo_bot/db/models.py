from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── messages ──────────────────────────────────────────────────────


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    voice_transcript: Mapped[str | None] = mapped_column(Text)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    is_forwarded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    forward_from_user_id: Mapped[int | None] = mapped_column(BigInteger)
    forward_is_from_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger)
    mentions_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    admin_mention_context: Mapped[str | None] = mapped_column(Text)
    media_group_id: Mapped[str | None] = mapped_column(String(64))
    ai_task_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_conflict_marker: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    edit_history: Mapped[dict | None] = mapped_column(JSONB)
    raw_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_messages_chat_timestamp", "chat_id", "timestamp"),
        Index("ix_messages_user_timestamp", "user_id", "timestamp"),
        Index("ix_messages_is_forwarded", "is_forwarded"),
        Index("ix_messages_mentions_admin", "mentions_admin"),
        Index("ix_messages_ai_task_detected", "ai_task_detected"),
        Index("ix_messages_ai_processed", "ai_processed"),
    )


# ── participants ──────────────────────────────────────────────────


class Participant(Base):
    __tablename__ = "participants"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    subscribed_to_recs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    first_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    total_messages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chats: Mapped[list[ParticipantChat]] = relationship(back_populates="participant")


# ── participant_chats ─────────────────────────────────────────────


class ParticipantChat(Base):
    __tablename__ = "participant_chats"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("participants.user_id"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    joined_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    participant: Mapped[Participant] = relationship(back_populates="chats")


# ── tasks ─────────────────────────────────────────────────────────


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assigner_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    assignee_user_id: Mapped[int | None] = mapped_column(BigInteger)
    task_text: Mapped[str] = mapped_column(Text, nullable=False)
    task_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    detection_method: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ai_context"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    due_date: Mapped[datetime.date | None] = mapped_column(Date)
    detected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    close_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_status_check_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint("source_message_id", "task_text_hash", name="uq_task_source_hash"),
    )


# ── daily_reports ─────────────────────────────────────────────────


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    stats_json: Mapped[dict | None] = mapped_column(JSONB)
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    sent_to_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── rgo_recommendations ──────────────────────────────────────────


class RgoRecommendation(Base):
    __tablename__ = "rgo_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rgo_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("participants.user_id"), nullable=False
    )
    rec_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    recommendation_text: Mapped[str | None] = mapped_column(Text)
    morning_context_summary: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )


# ── admin_alerts ──────────────────────────────────────────────────


class AdminAlert(Base):
    __tablename__ = "admin_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trigger_message_id: Mapped[int | None] = mapped_column(BigInteger)
    description: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


# ── monitored_chats ──────────────────────────────────────────────


class MonitoredChat(Base):
    __tablename__ = "monitored_chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_title: Mapped[str | None] = mapped_column(String(256))
    rgo_user_id: Mapped[int | None] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── alert_keywords ───────────────────────────────────────────────


class AlertKeyword(Base):
    __tablename__ = "alert_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── api_usage ────────────────────────────────────────────────────


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    call_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, default=0
    )
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── scheduler_runs ───────────────────────────────────────────────


class SchedulerRun(Base):
    __tablename__ = "scheduler_runs"

    job_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_success_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_error: Mapped[str | None] = mapped_column(Text)


# ── meeting_summaries ───────────────────────────────────


class MeetingSummary(Base):
    __tablename__ = "meeting_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    audio_duration_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transcript_text: Mapped[str | None] = mapped_column(Text)
    summary_text: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="miniapp"
    )  # "miniapp" or "voice_msg"
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ── presentation_preferences ────────────────────────────


class PresentationPreference(Base):
    __tablename__ = "presentation_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    preferences_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )


# ── glossary_orders ────────────────────────────────────


class GlossaryOrder(Base):
    __tablename__ = "glossary_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transcript_text: Mapped[str | None] = mapped_column(Text)
    order_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_rgo_ids: Mapped[list | None] = mapped_column(JSONB)  # list of chat_ids, null = all
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # "active" / "done" / "cancelled"
    target_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
