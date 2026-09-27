import asyncio
import shutil
import tempfile
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


class RetryableProviderError(ProviderError):
    pass


class PassthroughProvider:
    """Leaves the signal untouched before the FFmpeg DSP stage."""

    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult:
        return EnhancementResult(input_path, "ffmpeg-dsp-baseline", 0.0)


class ElevenLabsVoiceIsolatorProvider:
    URL = "https://api.elevenlabs.io/v1/audio-isolation"

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
    ) -> None:
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY is required for the ElevenLabs provider")
        self.api_key = api_key
        self.client = client
        self.retry_delays = retry_delays

    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        client = self.client or httpx.AsyncClient(timeout=1200)
        try:
            for attempt in range(len(self.retry_delays) + 1):
                output_path.unlink(missing_ok=True)
                try:
                    with input_path.open("rb") as source:
                        async with client.stream(
                            "POST",
                            self.URL,
                            headers={"xi-api-key": self.api_key},
                            files={"audio": (input_path.name, source, "audio/wav")},
                        ) as response:
                            if response.status_code != 200:
                                error_class = (
                                    RetryableProviderError
                                    if response.status_code == 429 or response.status_code >= 500
                                    else ProviderError
                                )
                                raise error_class(
                                    f"ElevenLabs audio isolation returned HTTP {response.status_code}"
                                )
                            with output_path.open("wb") as output:
                                async for chunk in response.aiter_bytes():
                                    output.write(chunk)
                    if output_path.stat().st_size == 0:
                        raise RetryableProviderError("ElevenLabs audio isolation returned an empty file")
                    return EnhancementResult(
                        output_path, "elevenlabs-voice-isolator", time.monotonic() - started
                    )
                except (RetryableProviderError, httpx.TransportError) as error:
                    output_path.unlink(missing_ok=True)
                    if attempt >= len(self.retry_delays):
                        if isinstance(error, RetryableProviderError):
                            raise
                        raise ProviderError("ElevenLabs audio isolation transport error") from error
                    await asyncio.sleep(self.retry_delays[attempt])
            raise AssertionError("Unreachable provider retry state")
        finally:
            if self.client is None:
                await client.aclose()


class DeepFilterNetProvider:
    """Run the optional DeepFilterNet command line tool."""

    def __init__(
        self,
        executable: str = "deep-filter",
        *,
        timeout_seconds: int = 1200,
    ) -> None:
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    async def enhance(
        self, input_path: Path, *, profile: ProcessingProfile, output_path: Path
    ) -> EnhancementResult:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="deepfilter-", dir=output_path.parent) as temp:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.executable,
                    "--compensate-delay",
                    "--output-dir",
                    temp,
                    str(input_path),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except FileNotFoundError as error:
                raise ProviderError("DeepFilterNet executable is not installed") from error
            try:
                await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
            except TimeoutError as error:
                process.kill()
                await process.wait()
                raise ProviderError("DeepFilterNet timed out") from error
            if process.returncode != 0:
                raise ProviderError(f"DeepFilterNet exited with code {process.returncode}")
            produced = Path(temp) / input_path.name
            if not produced.is_file() or produced.stat().st_size == 0:
                raise ProviderError("DeepFilterNet did not produce an audio file")
            shutil.move(str(produced), output_path)
        return EnhancementResult(output_path, "deepfilternet", time.monotonic() - started)


def select_provider(
    name: str,
    *,
    elevenlabs_api_key: str = "",
    deepfilter_bin: str = "deep-filter",
) -> SpeechEnhancementProvider:
    if name == "ffmpeg":
        return PassthroughProvider()
    if name == "elevenlabs":
        return ElevenLabsVoiceIsolatorProvider(elevenlabs_api_key)
    if name == "deepfilter":
        return DeepFilterNetProvider(deepfilter_bin)
    raise ValueError(f"Unsupported enhancement provider: {name}")
