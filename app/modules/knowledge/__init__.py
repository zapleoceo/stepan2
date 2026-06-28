"""Knowledge module — branch persona + product cards for LLM prompt context."""
from .repository import KnowledgeRepo, ProductRepo
from .service import PERSONA_SLUG, KnowledgeService

__all__ = ["KnowledgeRepo", "KnowledgeService", "PERSONA_SLUG", "ProductRepo"]
