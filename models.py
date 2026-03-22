"""
SQLAlchemy models for the support agent database.
"""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from database import Base


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(Integer, primary_key=True, index=True)
    gmail_message_id = Column(String, unique=True, index=True)
    gmail_thread_id = Column(String, index=True)

    # Customer info
    customer_email = Column(String, index=True)
    customer_name = Column(String)
    subject = Column(String)
    body = Column(Text)
    language = Column(String, default="pt-BR")

    # Classification
    category = Column(String)  # STATUS_PEDIDO, TROCA_DEVOLUCAO, etc.

    # Shopify data
    shopify_order_number = Column(String, nullable=True)
    shopify_data = Column(JSON, nullable=True)

    # Agent response
    draft_response = Column(Text, nullable=True)
    final_response = Column(Text, nullable=True)

    # Status: new, processing, draft_ready, approved, sent, rejected
    status = Column(String, default="new", index=True)

    # Timestamps
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    logs = relationship("TicketLog", back_populates="ticket", order_by="TicketLog.created_at")

    def to_dict(self):
        return {
            "id": self.id,
            "gmail_message_id": self.gmail_message_id,
            "gmail_thread_id": self.gmail_thread_id,
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "subject": self.subject,
            "body": self.body,
            "language": self.language,
            "category": self.category,
            "shopify_order_number": self.shopify_order_number,
            "shopify_data": self.shopify_data,
            "draft_response": self.draft_response,
            "final_response": self.final_response,
            "status": self.status,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


class TicketLog(Base):
    __tablename__ = "ticket_logs"

    id = Column(Integer, primary_key=True, index=True)
    ticket_id = Column(Integer, ForeignKey("support_tickets.id"), index=True)
    action = Column(String)  # created, classified, shopify_queried, draft_generated, approved, edited, sent, rejected
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    ticket = relationship("SupportTicket", back_populates="logs")
