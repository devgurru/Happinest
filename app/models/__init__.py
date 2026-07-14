"""
Models package — import all models here so Alembic can auto-detect them.
All models share the same Base from app.models.base.
"""
from app.models.base import Base  # noqa: F401

# Core session tables
from app.models.session import Session  # noqa: F401
from app.models.session_message import SessionMessage  # noqa: F401
from app.models.session_stage_history import SessionStageHistory  # noqa: F401
from app.models.session_memory_version import SessionMemoryVersion  # noqa: F401
from app.models.session_memory_patch import SessionMemoryPatch  # noqa: F401
from app.models.generated_artifact import GeneratedArtifact  # noqa: F401

# Reference data
from app.models.event_site import EventSite  # noqa: F401
from app.models.vendor import Vendor  # noqa: F401

# Recommendation junction tables
from app.models.session_event_site_recommendation import SessionEventSiteRecommendation  # noqa: F401
from app.models.session_vendor_recommendation import SessionVendorRecommendation  # noqa: F401

# Observability
from app.models.ai_turn_log import AiTurnLog  # noqa: F401
