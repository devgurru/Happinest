import copy
from app.models.wedding_profile import REQUIRED_FIELDS


def _get_nested(data: dict, dot_path: str):
    """Retrieve a nested value using a dot-separated path, e.g. 'couple.bride'."""
    keys = dot_path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def merge_profile(existing: dict, updates: dict) -> dict:
    """
    Deep-merge `updates` into `existing`.
    - Scalars are overwritten only if the new value is non-null and non-empty.
    - Lists are union-merged (no duplicates).
    - Nested dicts are merged recursively.
    """
    merged = copy.deepcopy(existing)

    for key, value in updates.items():
        if key not in merged:
            # New key — add it directly
            merged[key] = value
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_profile(merged[key], value)
        elif isinstance(value, list) and isinstance(merged.get(key), list):
            existing_list = merged[key]
            for item in value:
                if item not in existing_list:
                    existing_list.append(item)
            merged[key] = existing_list
        elif value not in (None, "", []):
            merged[key] = value

    return merged


def calculate_completion(profile: dict) -> float:
    """
    Calculate what percentage of required fields are filled.
    Returns a float 0.0 – 100.0.
    """
    if not REQUIRED_FIELDS:
        return 0.0

    filled = sum(
        1
        for field_path in REQUIRED_FIELDS
        if _get_nested(profile, field_path) not in (None, "", [])
    )
    return round((filled / len(REQUIRED_FIELDS)) * 100, 1)
