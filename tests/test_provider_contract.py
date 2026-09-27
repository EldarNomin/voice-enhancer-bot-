from pathlib import Path

import httpx
import pytest

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.infrastructure.providers import (
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
        provider = ElevenLabsVoiceIsolatorProvider("test-key", client=client)
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
