from dataclasses import dataclass
from enum import StrEnum


class Preset(StrEnum):
    NATURAL = "natural"
    STUDIO = "studio"
    REELS = "reels"
    PODCAST = "podcast"


@dataclass(frozen=True, slots=True)
class ProcessingProfile:
    preset: Preset
    enhancement_strength: float
    ambience_retention: float
    noise_reduction: float
    de_reverb: float
    presence: float
    warmth: float
    compression: float
    de_esser: float
    target_lufs: float = -14.0
    true_peak_db: float = -1.0

    def __post_init__(self) -> None:
        bounded = (
            "enhancement_strength",
            "ambience_retention",
            "noise_reduction",
            "de_reverb",
            "presence",
            "warmth",
            "compression",
            "de_esser",
        )
        for field in bounded:
            value = getattr(self, field)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field} must be between 0 and 1")
        if not -30.0 <= self.target_lufs <= -5.0:
            raise ValueError("target_lufs must be between -30 and -5 LUFS")
        if not -9.0 <= self.true_peak_db <= 0.0:
            raise ValueError("true_peak_db must be between -9 and 0 dBTP")


PROFILES: dict[Preset, ProcessingProfile] = {
    Preset.NATURAL: ProcessingProfile(
        Preset.NATURAL, 0.35, 0.45, 0.45, 0.25, 0.30, 0.35, 0.35, 0.30
    ),
    Preset.STUDIO: ProcessingProfile(Preset.STUDIO, 0.75, 0.15, 0.75, 0.65, 0.60, 0.45, 0.65, 0.50),
    Preset.REELS: ProcessingProfile(Preset.REELS, 0.80, 0.10, 0.80, 0.60, 0.75, 0.30, 0.75, 0.55),
    Preset.PODCAST: ProcessingProfile(
        Preset.PODCAST, 0.65, 0.25, 0.65, 0.50, 0.45, 0.55, 0.70, 0.55
    ),
}


def profile_for(preset: Preset) -> ProcessingProfile:
    return PROFILES[preset]
