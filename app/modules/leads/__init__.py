"""Leads module — identity resolution, inbound ingest, follow-up channel routing."""
from .identity import IdentityService
from .ingest import IngestService
from .phone import normalize_phone
from .router import FollowupRouter, RoutableThread

__all__ = [
    "FollowupRouter",
    "IdentityService",
    "IngestService",
    "RoutableThread",
    "normalize_phone",
]
