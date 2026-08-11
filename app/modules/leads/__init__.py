"""Leads module — identity resolution, inbound ingest, follow-up channel routing."""
from app.domain.phone import extract_phone, normalize_phone

from .identity import IdentityService
from .ingest import IngestService
from .profiles import ProfileService
from .router import FollowupRouter, RoutableThread

__all__ = [
    "FollowupRouter",
    "IdentityService",
    "IngestService",
    "ProfileService",
    "RoutableThread",
    "extract_phone",
    "normalize_phone",
]
