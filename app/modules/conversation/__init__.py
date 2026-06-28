"""Conversation module — decide a reply from dialog+KB, queue it, then send via channel."""
from .decision import Decision, parse_decision
from .outbox import OutboxSender
from .reply import ReplyService

__all__ = ["Decision", "OutboxSender", "ReplyService", "parse_decision"]
