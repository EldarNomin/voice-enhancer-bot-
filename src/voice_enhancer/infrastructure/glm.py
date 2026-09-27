"""Optional text-to-profile adapter; never receives or processes media."""

import json

import httpx

from voice_enhancer.domain.profile import ProcessingProfile
from voice_enhancer.domain.profile_patch import ADJUSTABLE_FIELDS, apply_profile_patch


class GlmInstructionError(RuntimeError):
    pass


class GlmProfileInterpreter:
    URL = "https://api.z.ai/api/paas/v4/chat/completions"
    MODEL = "glm-5.3-flash"

    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None) -> None:
        if not api_key:
            raise ValueError("GLM_API_KEY is required for text instructions")
        self.api_key = api_key
        self.client = client

    async def interpret(
        self, instruction: str, profile: ProcessingProfile
    ) -> tuple[ProcessingProfile, dict[str, float]]:
        if not 1 <= len(instruction.strip()) <= 500:
            raise ValueError("Instruction must contain 1 to 500 characters")
        fields = ", ".join(sorted(ADJUSTABLE_FIELDS))
        client = self.client or httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(
                self.URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Convert the user's audio-editing request to a JSON object of relative "
                                f"adjustments. Allowed fields only: {fields}. "
                                "Use signed decimal strings, e.g. {\"warmth\": \"+0.15\"}. "
                                "Each adjustment must be between -0.30 and +0.30. "
                                "Only include changes requested by the user. "
                                "Do not output shell commands, FFmpeg arguments, or commentary."
                            ),
                        },
                        {"role": "user", "content": instruction.strip()},
                    ],
                    "response_format": {"type": "json_object"},
                    "reasoning_effort": "low",
                    "max_tokens": 256,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            patch = json.loads(content)
            return apply_profile_patch(profile, patch)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
            raise GlmInstructionError("Could not interpret audio instruction") from error
        finally:
            if self.client is None:
                await client.aclose()
