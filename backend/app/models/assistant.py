"""个人研究运行。所有可恢复内容均为 JSON，进程内任务不进入存储。"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from core.database import Base


class AssistantRun(Base):
    __tablename__ = 'assistant_runs'
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey('chat_sessions.id', ondelete='CASCADE'), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    query = Column(Text, nullable=False)
    status = Column(String(24), default='queued', nullable=False)
    state = Column(JSONB, nullable=False, default=dict)
    events = Column(JSONB, nullable=False, default=list)
    report = Column(Text, default='')
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SessionContext(Base):
    __tablename__ = 'assistant_session_contexts'
    session_id = Column(UUID(as_uuid=True), ForeignKey('chat_sessions.id', ondelete='CASCADE'), primary_key=True)
    summary = Column(Text, default='')
    through_message_id = Column(UUID(as_uuid=True), nullable=True)
