from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from voice_enhancer.domain.profile import ProcessingProfile


@dataclass(frozen=True, slots=True)
class EnhancementResult:
    output_path: Path
    provider_name: str
    compute_seconds: float
    estimated_cost_usd: float = 0.0


class SpeechEnhancementProvider(Protocol):
    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult: ...
