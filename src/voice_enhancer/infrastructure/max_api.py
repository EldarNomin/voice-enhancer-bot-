"""Small MAX adapter against max-messenger/api-schema; no third-party bot framework."""

import asyncio
import ssl
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import httpx

from voice_enhancer.application.media_validation import MediaValidationError


class MaxAPIError(RuntimeError):
    def __init__(self, status: int, code: str = "unknown") -> None:
        self.status = status
        self.code = code
        # Never log signed URLs, tokens or full provider responses.
        super().__init__(f"MAX request failed: HTTP {status}, code {code}")


class MaxClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://platform-api2.max.ru",
        ca_bundle: str | None = None,
        media_hosts: tuple[str, ...] = ("max.ru", "okcdn.ru", "mycdn.me", "userapi.com"),
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ValueError("MAX_BOT_TOKEN is required")
        if urlsplit(base_url).scheme != "https":
            raise ValueError("MAX API requires HTTPS")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.media_hosts = tuple(h.strip().lower() for h in media_hosts if h.strip())
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(120, connect=15),
            verify=ssl.create_default_context(cafile=ca_bundle),
            follow_redirects=False,
        )
        self.owns_client = client is None

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> dict:
        response = await self.client.request(
            method, self.base_url + path, headers={"Authorization": self.token}, **kwargs
        )
        try:
            data = response.json()
        except ValueError:
            raise MaxAPIError(response.status_code, "invalid_response") from None
        if response.is_error or data.get("success") is False or "code" in data:
            code = str(data.get("code", "unknown"))
            # An error code is a short identifier, not arbitrary provider text.
            if not code.replace(".", "").replace("_", "").isalnum():
                code = "unknown"
            raise MaxAPIError(response.status_code, code[:64])
        return data

    async def send(self, chat_id: int, text: str, attachments: list | None = None) -> str:
        data = await self.request(
            "POST",
            "/messages",
            params={"chat_id": chat_id},
            json={"text": text, "attachments": attachments or []},
        )
        return str(data["message"]["body"]["mid"])

    async def edit(self, message_id: str, text: str) -> None:
        await self.request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json={"text": text, "attachments": []},
        )

    async def answer(self, callback_id: str, text: str = "") -> None:
        await self.request(
            "POST", "/answers", params={"callback_id": callback_id}, json={"notification": text}
        )

    def validate_media_url(self, url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not any(host == h or host.endswith("." + h) for h in self.media_hosts)
        ):
            raise ValueError("MAX media URL is outside the configured HTTPS CDN allowlist")

    async def download(self, url: str, target: Path, limit: int) -> None:
        # No API Authorization header on media/CDN requests, even after redirects.
        for _ in range(5):
            self.validate_media_url(url)
            async with self.client.stream("GET", url) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                if response.is_error:
                    raise MaxAPIError(response.status_code, "download_failed")
                if int(response.headers.get("content-length", 0)) > limit:
                    raise MediaValidationError("file_too_large")
                size = 0
                try:
                    with target.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > limit:
                                raise MediaValidationError("file_too_large")
                            output.write(chunk)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                if not size:
                    target.unlink(missing_ok=True)
                    raise MediaValidationError("read_error")
                return
        raise MaxAPIError(0, "too_many_redirects")

    async def attachment_url(self, attachment: dict) -> str:
        payload = attachment.get("payload") or {}
        if attachment["type"] == "video":
            token = quote(str(payload["token"]), safe="")
            data = await self.request("GET", f"/videos/{token}")
            urls = data.get("urls") or {}
            for resolution in (1080, 720, 480, 360, 240, 144):
                if urls.get(f"mp4_{resolution}"):
                    return urls[f"mp4_{resolution}"]
            raise MediaValidationError("read_error")
        return str(payload["url"])

    async def upload(self, path: Path, kind: str) -> dict:
        endpoint = await self.request("POST", "/uploads", params={"type": kind})
        self.validate_media_url(endpoint["url"])
        with path.open("rb") as source:
            response = await self.client.post(
                endpoint["url"], files={"data": (path.name, source)}, timeout=3600
            )
        if response.is_error or response.is_redirect:
            raise MaxAPIError(response.status_code, "upload_failed")
        # Video/audio token comes from /uploads; files from the multipart result.
        token = endpoint.get("token")
        if not token:
            token = response.json().get("token")
        if not token:
            raise MaxAPIError(0, "missing_upload_token")
        return {"type": kind, "payload": {"token": token}}

    async def send_media(
        self, chat_id: int, path: Path, kind: str, text: str, keyboard: dict
    ) -> str:
        limit = (250 if kind == "video" else 256) * 1024**2
        # Preserve large video/audio as a downloadable original file.
        upload_kind = kind if path.stat().st_size <= limit else "file"
        attachment = await self.upload(path, upload_kind)
        for attempt in range(7):
            try:
                return await self.send(chat_id, text, [attachment, keyboard])
            except MaxAPIError as error:
                if error.code != "attachment.not.ready" or attempt == 6:
                    raise
                await asyncio.sleep(min(2**attempt, 16))
        raise AssertionError("Unreachable")
