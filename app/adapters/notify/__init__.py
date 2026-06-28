"""Notify adapters — transports for the NotifierPort (Telegram today, more later)."""
from .telegram import TelegramNotifier

__all__ = ["TelegramNotifier"]
