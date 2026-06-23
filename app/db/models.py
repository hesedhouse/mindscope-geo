from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Date,
    ForeignKey, JSON, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    hashed_password = Column(String(512), nullable=False)
    company_name = Column(String(200), nullable=True)
    is_active = Column(Boolean, default=True)
    plan = Column(String(50), default="free")  # free, starter, pro, enterprise
    created_at = Column(DateTime, default=datetime.utcnow)

    clients = relationship("Client", back_populates="user")


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(200), nullable=False)
    domain = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="clients")
    brands = relationship("Brand", back_populates="client", cascade="all, delete-orphan")


class Brand(Base):
    __tablename__ = "brands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    name = Column(String(200), nullable=False)
    competitors = Column(JSON, default=list)
    keywords = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="brands")
    scan_prompts = relationship("ScanPrompt", back_populates="brand", cascade="all, delete-orphan")
    visibility_scores = relationship("VisibilityScore", back_populates="brand", cascade="all, delete-orphan")


class ScanPrompt(Base):
    __tablename__ = "scan_prompts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    prompt_text = Column(Text, nullable=False)
    category = Column(String(50), default="추천")
    is_active = Column(Boolean, default=True)

    brand = relationship("Brand", back_populates="scan_prompts")
    scan_results = relationship("ScanResult", back_populates="scan_prompt", cascade="all, delete-orphan")


class ScanResult(Base):
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scan_prompt_id = Column(Integer, ForeignKey("scan_prompts.id"), nullable=False)
    engine = Column(String(50), nullable=False)
    response_text = Column(Text, nullable=False)
    brand_mentioned = Column(Boolean, default=False)
    mention_count = Column(Integer, default=0)
    sentiment_score = Column(Float, nullable=True)
    citation_urls = Column(JSON, default=list)
    scanned_at = Column(DateTime, default=datetime.utcnow)

    scan_prompt = relationship("ScanPrompt", back_populates="scan_results")


class VisibilityScore(Base):
    __tablename__ = "visibility_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    brand_id = Column(Integer, ForeignKey("brands.id"), nullable=False)
    engine = Column(String(50), nullable=False)
    score = Column(Float, default=0.0)
    share_of_voice = Column(Float, default=0.0)
    avg_sentiment = Column(Float, default=0.0)
    total_prompts = Column(Integer, default=0)
    mentioned_prompts = Column(Integer, default=0)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    calculated_at = Column(DateTime, default=datetime.utcnow)

    brand = relationship("Brand", back_populates="visibility_scores")
