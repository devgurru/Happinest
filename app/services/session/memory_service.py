"""
Memory Service — versioned canonical planner memory.
Backend owns all memory. AI proposes patches. Backend applies them.
"""
import copy
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import StaleSectionId
from app.domain.memory_schema import update_committed_selections
from app.models.session import Session
from app.models.session_memory_patch import SessionMemoryPatch
from app.models.session_memory_version import SessionMemoryVersion

# Invalidation rules (from doc 05)
_INVALIDATION_MAP: dict[str, list[str]] = {
    "identity":           ["brief", "direction", "summary"],
    "occasion":           ["brief", "direction", "budget", "vendors", "summary"],
    "personality":        ["brief", "direction", "summary"],
    "vibe":               ["brief", "direction", "budget", "summary"],
    "logistics.events":   ["budget", "vendors", "summary"],
    "logistics.guestCounts": ["budget", "vendors", "summary"],
    "logistics.budget":   ["vendors", "summary"],
}


VALID_STALE = {s.value for s in StaleSectionId}


def deep_merge(base: dict, patch: dict, is_correction: bool = False) -> dict:
    """
    Recursively merge patch into base.
    - Dict values are merged recursively
    - List values in patch MERGE (extend & deduplicate) with base lists unless is_correction is True
    - None / empty-string patch values are skipped
    """
    result = copy.deepcopy(base)
    for key, val in patch.items():
        if val is None:
            continue
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], val, is_correction=is_correction)
        elif isinstance(val, list) and isinstance(result.get(key), list) and not is_correction:
            existing = copy.deepcopy(result[key])
            for item in val:
                if item not in existing and (not isinstance(item, str) or item.strip()):
                    existing.append(copy.deepcopy(item))
            result[key] = existing
        else:
            result[key] = copy.deepcopy(val)

    # Safety check: If occasion.place exists in merged memory, ensure country is updated to match the place
    if "occasion" in patch and isinstance(result.get("occasion"), dict):
        occ = result["occasion"]
        place = (occ.get("place") or "").strip()
        if place:
            from app.utils.validators import infer_country_from_place
            inferred = infer_country_from_place(place)
            patch_country = (patch.get("occasion", {}).get("country") or "").strip()
            if patch_country:
                occ["country"] = patch_country
            elif inferred:
                occ["country"] = inferred

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
        *,
        request_id: uuid.UUID | str,
        open_questions: list | None = None,
        extra_stale: list[str] | None = None,
        is_correction: bool = False,
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
        is_corr = bool(
            is_correction or
            patch.get("is_correction") or
            patch.get("metaIntent") == "correction"
        )
        new_memory = deep_merge(current.memory_json, patch, is_correction=is_corr)

        # On correction turn, clean removed items from earlySignals and committedSelections
        if is_corr:
            if "personality" in patch and isinstance(patch["personality"], dict):
                new_tags = patch["personality"].get("tags")
                if isinstance(new_tags, list):
                    new_tags_lower = {t.lower() for t in new_tags if isinstance(t, str)}
                    if "earlySignals" in new_memory and isinstance(new_memory["earlySignals"], dict):
                        early_p = new_memory["earlySignals"].get("personality") or []
                        new_memory["earlySignals"]["personality"] = [
                            t for t in early_p if isinstance(t, str) and t.lower() in new_tags_lower
                        ]
                    if "committedSelections" in new_memory and isinstance(new_memory["committedSelections"], dict):
                        new_memory["committedSelections"]["personality"] = list(new_tags)

            if "vibe" in patch and isinstance(patch["vibe"], dict):
                vibe_patch = patch["vibe"]
                primary = vibe_patch.get("primaryVibe")
                secondaries = vibe_patch.get("secondaryVibes") or []
                all_vibes = ([primary] if primary else []) + (secondaries if isinstance(secondaries, list) else [])
                all_vibes_lower = {v.lower() for v in all_vibes if isinstance(v, str)}
                if "earlySignals" in new_memory and isinstance(new_memory["earlySignals"], dict):
                    early_v = new_memory["earlySignals"].get("vibe") or []
                    new_memory["earlySignals"]["vibe"] = [
                        v for v in early_v if isinstance(v, str) and v.lower() in all_vibes_lower
                    ]
                if "committedSelections" in new_memory and isinstance(new_memory["committedSelections"], dict):
                    new_memory["committedSelections"]["vibe"] = list(all_vibes)

            if "logistics" in patch and isinstance(patch["logistics"], dict) and "events" in patch["logistics"]:
                new_events = patch["logistics"].get("events") or []
                if isinstance(new_events, list):
                    new_events_lower = {e.lower() for e in new_events if isinstance(e, str)}
                    if "earlySignals" in new_memory and isinstance(new_memory["earlySignals"], dict):
                        early_e = new_memory["earlySignals"].get("events") or []
                        new_memory["earlySignals"]["events"] = [
                            e for e in early_e if isinstance(e, str) and e.lower() in new_events_lower
                        ]
                    if "committedSelections" in new_memory and isinstance(new_memory["committedSelections"], dict):
                        new_memory["committedSelections"]["events"] = list(new_events)

        # Synchronize and prune events, guestCounts & vendorPreferences when logistics.events exists in memory
        if "logistics" in new_memory and isinstance(new_memory["logistics"], dict):
            logistics = new_memory["logistics"]
            events_list = logistics.get("events") or []
            if isinstance(events_list, list) and events_list:
                # 0. Clean out individual constituent events if a combined event (with '/') exists
                combined_events = [e for e in events_list if isinstance(e, str) and "/" in e]
                if combined_events:
                    constituent_lower = set()
                    for cb in combined_events:
                        for part in cb.split("/"):
                            if part.strip():
                                constituent_lower.add(part.strip().lower())

                    events_list = [
                        e for e in events_list
                        if not (isinstance(e, str) and "/" not in e and e.strip().lower() in constituent_lower)
                    ]
                    logistics["events"] = events_list

                valid_event_set = {str(e).strip().lower() for e in events_list if isinstance(e, str)}

                # 1. Carry constituent counts to combined events (e.g. "Wedding Ceremony/Reception")
                counts = logistics.get("guestCounts")
                if isinstance(counts, dict):
                    for ev in events_list:
                        if isinstance(ev, str) and "/" in ev and ev not in counts:
                            parts = {p.strip().lower() for p in ev.split("/") if p.strip()}
                            part_counts = [int(counts[k]) for k in list(counts) if k.lower() in parts and isinstance(counts[k], (int, float)) and counts[k] > 0]
                            if part_counts:
                                counts[ev] = max(part_counts)

                    # Prune guestCounts for events no longer in the list
                    logistics["guestCounts"] = {
                        ev: cnt for ev, cnt in counts.items()
                        if str(ev).strip().lower() in valid_event_set
                    }

                # 2. Prune vendorPreferences for deleted events
                vendors = logistics.get("vendorPreferences")
                if isinstance(vendors, dict):
                    logistics["vendorPreferences"] = {
                        ev: v_val for ev, v_val in vendors.items()
                        if str(ev).strip().lower() in valid_event_set
                    }



        # Fold legacy top-level occasion fields into occasion.{...}
        from app.utils.validators import get_occasion_state
        occ_state = get_occasion_state(new_memory)
        new_occ = occ_state["occasion"]
        valid_occ_keys = {
            "place", "locationPreference", "settingPreference",
            "datePreference", "seasonPreference", "destinationMode",
            "specificityLevel", "country"
        }
        for k in list(new_occ.keys()):
            if k not in valid_occ_keys:
                new_occ.pop(k, None)
        new_memory["occasion"] = new_occ

        for legacy_key in (
            "place", "datePreference", "seasonPreference",
            "locationPreference", "settingPreference", "destinationMode",
        ):
            if legacy_key in new_memory and legacy_key != "occasion":
                new_memory.pop(legacy_key, None)

        # Sanitize direction.options in memory: store ONLY the user-selected direction option
        dir_obj = new_memory.get("direction")
        if isinstance(dir_obj, dict):
            selected_id = (dir_obj.get("selectedDirectionId") or new_memory.get("committedSelections", {}).get("directionId") or "").strip()
            opts = dir_obj.get("options") or []
            if selected_id:
                selected_opts = [o for o in opts if isinstance(o, dict) and o.get("id") == selected_id]
                if not selected_opts:
                    selected_name = dir_obj.get("selectedDirectionName") or new_memory.get("committedSelections", {}).get("directionName", "")
                    selected_opts = [{"id": selected_id, "name": selected_name}]
                dir_obj["options"] = selected_opts
                if selected_opts and selected_opts[0].get("name"):
                    dir_obj["selectedDirectionName"] = selected_opts[0]["name"]
            else:
                dir_obj["options"] = []


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
