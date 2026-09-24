"""ORM-модели. users и schedule_files — по ТЗ, lessons — хранение разобранного расписания."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    course: Mapped[int | None] = mapped_column(Integer, nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    faculty: Mapped[str] = mapped_column(String(32), default="permskaya")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ScheduleFile(Base):
    __tablename__ = "schedule_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    schedule_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    file_type: Mapped[str] = mapped_column(String(16))          # schedule | changes
    url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    faculty: Mapped[str] = mapped_column(String(32), default="permskaya")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint(
            "schedule_date", "group_name", "lesson_number", "source_type",
            name="uq_lesson",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_date: Mapped[date] = mapped_column(Date, index=True)
    group_name: Mapped[str] = mapped_column(String(32), index=True)
    course: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson_number: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    teacher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    room: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(16))         # schedule | changes
    source_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule_files.id"), nullable=True
    )
    faculty: Mapped[str] = mapped_column(String(32), default="permskaya")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
