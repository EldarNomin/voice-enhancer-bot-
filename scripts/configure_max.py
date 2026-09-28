"""Inspect MAX connectivity or register the deployment's HTTPS webhook."""

import argparse
import asyncio
import re
from urllib.parse import urlsplit

from voice_enhancer.config import settings
from voice_enhancer.infrastructure.max_api import MaxClient


async def run(url: str | None) -> None:
    client = MaxClient(
        settings.max_bot_token,
        base_url=settings.max_api_base_url,
        ca_bundle=settings.max_ca_bundle,
        media_hosts=tuple(settings.max_media_hosts.split(",")),
    )
    try:
        bot = await client.request("GET", "/me")
        print(f"Connected to MAX bot ID {bot['user_id']}")
        if url is None:
            subscriptions = await client.request("GET", "/subscriptions")
            print(f"Active subscriptions: {len(subscriptions.get('subscriptions', []))}")
            return
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port is not None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path != "/webhooks/max"
        ):
            raise ValueError("Use https://your-domain/webhooks/max on implicit port 443")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", settings.max_webhook_secret):
            raise ValueError("MAX_WEBHOOK_SECRET must be 32–256 URL-safe characters")
        await client.request(
            "POST",
            "/subscriptions",
            json={
                "url": url,
                "secret": settings.max_webhook_secret,
                "update_types": ["message_created", "message_callback", "bot_started"],
            },
        )
        print("MAX webhook registered")
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webhook", help="Register this HTTPS endpoint; omitted = read-only check")
    asyncio.run(run(parser.parse_args().webhook))
