import json

import httpx
import pytest

from voice_enhancer.domain.profile import Preset, profile_for
from voice_enhancer.domain.profile_patch import apply_profile_patch
from voice_enhancer.infrastructure.glm import GlmInstructionError, GlmProfileInterpreter


def test_profile_patch_only_changes_active_bounded_fields() -> None:
    original = profile_for(Preset.STUDIO)
    patched, changes = apply_profile_patch(original, {"warmth": "+0.15", "presence": "-0.05"})
    assert original.warmth == 0.45
    assert patched.warmth == 0.60
    assert patched.presence == 0.55
    assert patched.noise_reduction == original.noise_reduction
    assert changes == {"warmth": 0.60, "presence": 0.55}


@pytest.mark.parametrize(
    "patch",
    [
        {},
        {"ffmpeg_args": "-y"},
        {"de_reverb": "+0.10"},
        {"warmth": "+0.31"},
        {"warmth": "1; rm -rf /"},
        {"warmth": 0.1},
        {"warmth": "+0.75"},
        {"warmth": "-0.50"},
    ],
)
def test_profile_patch_rejects_unsupported_or_unsafe_values(patch: object) -> None:
    with pytest.raises(ValueError):
        apply_profile_patch(profile_for(Preset.STUDIO), patch)


@pytest.mark.asyncio
async def test_glm_client_requests_json_and_validates_result() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"warmth":"+0.10"}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        profile, changes = await GlmProfileInterpreter("secret", client=client).interpret(
            "Сделай голос мягче", profile_for(Preset.STUDIO)
        )
    assert profile.warmth == 0.55
    assert changes == {"warmth": 0.55}
    assert requests[0].url.host == "api.z.ai"
    assert json.loads(requests[0].content)["model"] == "glm-5.3-flash"
    assert json.loads(requests[0].content)["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_glm_client_does_not_expose_provider_error_body() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500, text="private response"))
    ) as client:
        with pytest.raises(GlmInstructionError, match="Could not interpret") as caught:
            await GlmProfileInterpreter("secret", client=client).interpret(
                "Сделай голос мягче", profile_for(Preset.STUDIO)
            )
    assert "private response" not in str(caught.value)


@pytest.mark.asyncio
async def test_glm_client_rejects_model_attempt_to_control_ffmpeg() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"ffmpeg_args":"-y"}'}}]},
            )
        )
    ) as client:
        with pytest.raises(ValueError, match="unsupported"):
            await GlmProfileInterpreter("secret", client=client).interpret(
                "Change my audio", profile_for(Preset.STUDIO)
            )
