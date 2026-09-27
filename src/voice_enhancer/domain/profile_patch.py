"""Validated, deliberately small adjustments to the active FFmpeg profile controls."""

import re
from dataclasses import replace

from voice_enhancer.domain.profile import ProcessingProfile

ADJUSTABLE_FIELDS = frozenset({"warmth", "presence", "noise_reduction", "compression"})
DELTA_PATTERN = re.compile(r"^[+-](?:0(?:\.\d{1,2})?|1(?:\.0{1,2})?)$")
MAX_ABSOLUTE_DELTA = 0.30


def apply_profile_patch(
    profile: ProcessingProfile, raw_patch: object
) -> tuple[ProcessingProfile, dict[str, float]]:
    if not isinstance(raw_patch, dict) or not raw_patch:
        raise ValueError("Profile patch must be a non-empty JSON object")
    if set(raw_patch) - ADJUSTABLE_FIELDS:
        raise ValueError("Profile patch contains unsupported fields")
    changes: dict[str, float] = {}
    for field, raw_delta in raw_patch.items():
        if not isinstance(raw_delta, str) or DELTA_PATTERN.fullmatch(raw_delta) is None:
            raise ValueError(f"Invalid adjustment for {field}")
        delta = float(raw_delta)
        if abs(delta) > MAX_ABSOLUTE_DELTA:
            raise ValueError(f"Adjustment exceeds safe limit for {field}")
        value = round(getattr(profile, field) + delta, 2)
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"Adjustment exceeds profile bounds for {field}")
        changes[field] = value
    return replace(profile, **changes), changes
