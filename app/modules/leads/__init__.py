"""Leads module — identity resolution, inbound ingest, follow-up channel routing."""
from .identity import IdentityService
from .ingest import IngestService
from .phone import extract_phone, normalize_phone
from .router import FollowupRouter, RoutableThread

__all__ = [
    "FollowupRouter",
    "IdentityService",
    "IngestService",
    "RoutableThread",
    "extract_phone",
    "normalize_phone",
]
