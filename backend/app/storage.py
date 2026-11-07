import os
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, String, JSON, DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

Path('data').mkdir(exist_ok=True)
class Base(DeclarativeBase): pass
class ChecklistRow(Base):
    __tablename__ = 'checklists'
    id: Mapped[int] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)
class ConversationRow(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
class EvaluationRow(Base):
    __tablename__ = 'evaluations'
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    checklist_id: Mapped[int] = mapped_column(Integer)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    payload: Mapped[dict] = mapped_column(JSON)
class AppealRow(Base):
    __tablename__ = 'appeals'
    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(Integer)
    criterion_id: Mapped[str] = mapped_column(String)
    comment: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='open')
    decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)

def setup(url=None):
    url = url or os.getenv('DATABASE_URL', 'sqlite:///./data/cqa.db')
    engine = create_engine(url, connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)

class BudgetRow(Base):
    __tablename__ = 'budget'
    id: Mapped[int] = mapped_column(primary_key=True)
    reserved_microdollars: Mapped[int] = mapped_column(Integer, default=0)

class GatewayBudgetRow(Base):
    __tablename__ = 'gateway_budget'
    id: Mapped[int] = mapped_column(primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    reserved_credits: Mapped[int] = mapped_column(Integer, default=0)

class GatewayRequestRow(Base):
    __tablename__ = 'gateway_requests'
    id: Mapped[int] = mapped_column(primary_key=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    model: Mapped[str] = mapped_column(String)
    stage: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='pending')
    payload: Mapped[dict] = mapped_column(JSON)

class ExpertRow(Base):
    __tablename__ = 'expert_labels'
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    payload: Mapped[dict] = mapped_column(JSON)

class BenchmarkAnnotationRow(Base):
    __tablename__ = 'benchmark_annotations'
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, index=True)
    slot: Mapped[str] = mapped_column(String)
    reviewer: Mapped[str] = mapped_column(String)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    payload: Mapped[dict] = mapped_column(JSON)

class BenchmarkRunRow(Base):
    __tablename__ = 'benchmark_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    created: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    status: Mapped[str] = mapped_column(String, default='planned')
    payload: Mapped[dict] = mapped_column(JSON)

class RunBudgetRow(Base):
    __tablename__ = 'run_budgets'
    id: Mapped[int] = mapped_column(primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    request_limit: Mapped[int] = mapped_column(Integer)
    reserved_microusd: Mapped[int] = mapped_column(Integer, default=0)
    limit_microusd: Mapped[int] = mapped_column(Integer)
