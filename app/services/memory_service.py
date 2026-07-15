"""
Memory Service — versioned canonical planner memory.
Backend owns all memory. AI proposes patches. Backend applies them.
"""
import copy
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.enums import StaleSectionId
from app.domain.memory_schema import update_committed_selections
from app.models.session import Session
from app.models.session_memory_patch import SessionMemoryPatch
from app.models.session_memory_version import SessionMemoryVersion

# Invalidation rules (from doc 05)
_INVALIDATION_MAP: dict[str, list[str]] = {
    "occasion":           ["brief", "direction", "budget", "vendors", "summary"],
    "personality":        ["brief", "direction", "summary"],
    "vibe":               ["brief", "direction", "budget", "summary"],
    "logistics.events":   ["budget", "vendors", "summary"],
    "logistics.guestCounts": ["budget", "vendors", "summary"],
    "logistics.budget":   ["vendors", "summary"],
}

VALID_STALE = {s.value for s in StaleSectionId}


def deep_merge(base: dict, patch: dict) -> dict:
    """
    Recursively merge patch into base.
    - Dict values are merged recursively
    - List values in patch REPLACE (not extend) base lists
    - None / empty-string patch values are skipped
    """
    result = copy.deepcopy(base)
    for key, val in patch.items():
        if val is None:
            continue
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = copy.deepcopy(val)
    return result


def compute_stale_sections(patch: dict, current_stale: list[str]) -> list[str]:
    """
    Given a memory patch, compute which sections become stale.
    Merges with any already-stale sections.
    """
    new_stale = set(current_stale)
    for top_key, val in patch.items():
        if top_key in _INVALIDATION_MAP:
            for stale in _INVALIDATION_MAP[top_key]:
                new_stale.add(stale)
        # Handle logistics sub-keys
        if top_key == "logistics" and isinstance(val, dict):
            for sub_key in val:
                full_key = f"logistics.{sub_key}"
                if full_key in _INVALIDATION_MAP:
                    for stale in _INVALIDATION_MAP[full_key]:
                        new_stale.add(stale)
    return [s for s in new_stale if s in VALID_STALE]


class MemoryService:

    @staticmethod
    async def get_latest_memory(
        db: AsyncSession, session_id: uuid.UUID
    ) -> SessionMemoryVersion | None:
        result = await db.execute(
            select(SessionMemoryVersion)
            .where(SessionMemoryVersion.session_id == session_id)
            .order_by(SessionMemoryVersion.version_no.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_memory_at_version(
        db: AsyncSession, session_id: uuid.UUID, version_no: int
    ) -> SessionMemoryVersion | None:
        result = await db.execute(
            select(SessionMemoryVersion).where(
                SessionMemoryVersion.session_id == session_id,
                SessionMemoryVersion.version_no == version_no,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def apply_patch(
        db: AsyncSession,
        session: Session,
        patch: dict,
        request_id: uuid.UUID | None = None,
        open_questions: list | None = None,
        extra_stale: list[str] | None = None,
    ) -> SessionMemoryVersion:
        """
        Apply a validated patch to canonical memory.
        Creates a new version. Records the patch. Updates session.memory_version.
        """
        current = await MemoryService.get_latest_memory(db, session.id)
        if not current:
            raise ValueError(f"No memory found for session {session.id}")

        # Merge committed chip selections for UI restore
        patch = dict(patch)
        patch["committedSelections"] = update_committed_selections(current.memory_json, patch)

        # Merge
        new_memory = deep_merge(current.memory_json, patch)

        # Fold legacy top-level occasion fields into occasion.{...}
        from app.services.text_extract import get_occasion_state
        occ_state = get_occasion_state(new_memory)
        new_memory["occasion"] = occ_state["occasion"]
        for legacy_key in (
            "place", "datePreference", "seasonPreference",
            "locationPreference", "settingPreference", "destinationMode",
        ):
            if legacy_key in new_memory and legacy_key != "occasion":
                new_memory.pop(legacy_key, None)

        # Compute stale sections
        new_stale = compute_stale_sections(patch, current.stale_sections)
        if extra_stale:
            new_stale = list(set(new_stale) | set(extra_stale))

        # Update stale markers inside the memory blob too
        new_memory["staleSections"] = new_stale
        if open_questions is not None:
            new_memory["openQuestions"] = open_questions

        new_version_no = current.version_no + 1

        new_version = SessionMemoryVersion(
            session_id=session.id,
            version_no=new_version_no,
            memory_json=new_memory,
            stale_sections=new_stale,
            open_questions=open_questions or [],
            updated_by_request_id=request_id,
        )
        db.add(new_version)

        # Record the patch
        patch_record = SessionMemoryPatch(
            session_id=session.id,
            from_version_no=current.version_no,
            to_version_no=new_version_no,
            patch_json=patch,
            request_id=request_id,
        )
        db.add(patch_record)

        # Update session counter
        session.memory_version = new_version_no
        db.add(session)

        await db.flush()
        return new_version
