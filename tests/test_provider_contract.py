from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.providers import (
    DeepFilterNetProvider,
    ElevenLabsVoiceIsolatorProvider,
    ProviderError,
    select_provider,
)


@pytest.mark.asyncio
async def test_elevenlabs_adapter_sends_audio_and_saves_binary_response(tmp_path: Path) -> None:
    incoming = tmp_path / "voice.wav"
    incoming.write_bytes(b"source-audio")

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio-isolation"
        assert request.headers["xi-api-key"] == "test-key"
        assert b"source-audio" in request.read()
        return httpx.Response(200, content=b"isolated-audio")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = ElevenLabsVoiceIsolatorProvider("test-key", client=client)
        output = tmp_path / "isolated.audio"
        result = await provider.enhance(
            incoming, profile=profile_for(Preset.STUDIO), output_path=output
        )
    assert output.read_bytes() == b"isolated-audio"
    assert result.provider_name == "elevenlabs-voice-isolator"


@pytest.mark.asyncio
async def test_provider_error_does_not_include_response_body_or_key(tmp_path: Path) -> None:
    incoming = tmp_path / "voice.wav"
    incoming.write_bytes(b"source-audio")
    transport = httpx.MockTransport(lambda request: httpx.Response(429, content=b"private data"))
    async with httpx.AsyncClient(transport=transport) as client:
        provider = ElevenLabsVoiceIsolatorProvider("test-key", client=client, retry_delays=())
        with pytest.raises(ProviderError) as error:
            await provider.enhance(
                incoming, profile=profile_for(Preset.REELS), output_path=tmp_path / "result.audio"
            )
    assert "429" in str(error.value)
    assert "private data" not in str(error.value)
    assert "test-key" not in str(error.value)


def test_provider_selection_requires_key_when_elevenlabs_enabled() -> None:
    with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
        select_provider("elevenlabs")


@pytest.mark.asyncio
async def test_elevenlabs_retries_transient_error_and_succeeds(tmp_path: Path) -> None:
    incoming = tmp_path / "voice.wav"
    incoming.write_bytes(b"source-audio")
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls == 1 else httpx.Response(200, content=b"enhanced")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        provider = ElevenLabsVoiceIsolatorProvider("test-key", client=client, retry_delays=(0,))
        result = await provider.enhance(
            incoming, profile=profile_for(Preset.STUDIO), output_path=tmp_path / "result.audio"
        )
    assert calls == 2
    assert result.output_path.read_bytes() == b"enhanced"


@pytest.mark.asyncio
async def test_deepfilter_reports_missing_executable_without_leaking_audio_path(
    tmp_path: Path,
) -> None:
    provider = DeepFilterNetProvider(executable="definitely-not-installed-deepfilter")
    with pytest.raises(ProviderError, match="executable is not installed") as error:
        await provider.enhance(
            tmp_path / "private-voice.wav",
            profile=profile_for(Preset.STUDIO),
            output_path=tmp_path / "isolated.wav",
        )
    assert "private-voice" not in str(error.value)


@pytest.mark.asyncio
async def test_deepfilter_collects_cli_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = tmp_path / "voice.wav"
    incoming.write_bytes(b"audio")

    async def fake_exec(*args, **kwargs):
        output_dir = Path(args[args.index("--output-dir") + 1])
        (output_dir / incoming.name).write_bytes(b"enhanced")

        async def wait():
            return 0

        return SimpleNamespace(wait=wait, returncode=0)

    monkeypatch.setattr("voice_enhancer.infrastructure.providers.asyncio.create_subprocess_exec", fake_exec)
    output = tmp_path / "isolated.audio"
    result = await DeepFilterNetProvider().enhance(
        incoming, profile=profile_for(Preset.NATURAL), output_path=output
    )
    assert output.read_bytes() == b"enhanced"
    assert result.provider_name == "deepfilternet"
    assert not list(tmp_path.glob("deepfilter-*"))
