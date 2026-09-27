import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from voice_enhancer.domain.profile import ProcessingProfile


@dataclass(frozen=True, slots=True)
class EnhancementResult:
    output_path: Path
    provider_name: str
    compute_seconds: float
    estimated_cost_usd: float | None = None


class SpeechEnhancementProvider(Protocol):
    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult: ...


class ProviderError(RuntimeError):
    pass


class PassthroughProvider:
    """Leaves the signal untouched before the FFmpeg DSP stage."""

    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult:
        return EnhancementResult(input_path, "ffmpeg-dsp-baseline", 0.0)


class ElevenLabsVoiceIsolatorProvider:
    URL = "https://api.elevenlabs.io/v1/audio-isolation"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for the ElevenLabs provider")
        self.api_key = api_key
        self.client = client

    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        client = self.client or httpx.AsyncClient(timeout=1200)
        try:
            with input_path.open("rb") as source:
                async with client.stream(
                    "POST",
                    self.URL,
                    headers={"xi-api-key": self.api_key},
                    files={"audio": (input_path.name, source, "audio/wav")},
                ) as response:
                    if response.status_code != 200:
                        raise ProviderError(
                            f"ElevenLabs audio isolation returned HTTP {response.status_code}"
                        )
                    with output_path.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            output.write(chunk)
            if output_path.stat().st_size == 0:
                raise ProviderError("ElevenLabs audio isolation returned an empty file")
            return EnhancementResult(
                output_path, "elevenlabs-voice-isolator", time.monotonic() - started
            )
        finally:
            if self.client is None:
                await client.aclose()


def select_provider(name: str, *, elevenlabs_api_key: str = "") -> SpeechEnhancementProvider:
    if name == "ffmpeg":
        return PassthroughProvider()
    if name == "elevenlabs":
        return ElevenLabsVoiceIsolatorProvider(elevenlabs_api_key)
    raise ValueError(f"Unsupported enhancement provider: {name}")
