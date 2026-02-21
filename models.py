from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String, unique=True, index=True)
    host = Column(String, index=True)
    trigger_name = Column(String)
    status = Column(String)
    severity = Column(String)
    raw_data = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())