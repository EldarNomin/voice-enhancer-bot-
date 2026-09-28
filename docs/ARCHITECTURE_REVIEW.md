# Architecture review — 2026-09-27

## Decision

Keep the current stack for the private alpha. Python + aiogram 3, SQLAlchemy/asyncpg + PostgreSQL, Redis, FFmpeg and a separate worker fit this workload. Audio inference is a pluggable adapter. GLM translates text into bounded profile changes and must remain outside the default processing path. There is no reason to wait for its payment or rewrite the bot in another language.

| Component | Role and trade-off |
| --- | --- |
| aiogram bot | Telegram UI and validated uploads; no domain dependency on Telegram types. Download/probe still runs in handlers, so public launch needs admission/concurrency limits. |
| PostgreSQL | Authoritative job state, claims, events and measured resource use. Unique update IDs and conditional updates prevent duplicate processing. |
| Redis | Delivery hints for durable database jobs. Atomic deduplication limits repeated reconciliation entries; PostgreSQL repairs missing queue entries. Celery/another queue framework is unnecessary for one worker. |
| Worker | One process on a shared local Linux volume, protected by an OS file lock before recovery. Recovery has a persistent retry budget. Scaling needs leases/heartbeats rather than resetting every in-progress job. |
| FFmpeg | Extract, DSP and remux. Audio offsets are preserved when WAV drops timestamps. AAC and video are copied during remux to avoid a second lossy audio encoding. |
| Provider | FFmpeg baseline, DeepFilterNet CPU or optional ElevenLabs. Do not equate technical validity with better sounding speech. |
| FastAPI | MAX authenticated webhook with a durable PostgreSQL inbox. `/health` is only liveness; it does not certify worker, database or Telegram readiness. Bound to localhost in Compose. |
| Storage | Local shared volume is appropriate for one-host alpha. S3, quotas, backups and explicit Telegram-cache retention are release work. |

## Fixes in this review

- Restore `.env.example`; route bot and worker to local Telegram API explicitly in Compose.
- Exclude credentials and media from Docker context; restart state services after host reboot.
- Pin Python runtime dependencies and add an optional checksum-pinned DeepFilterNet Docker target.
- Bound attempts across worker crashes; prevent a second local worker from resetting active jobs.
- Reconcile PostgreSQL jobs periodically under load and deduplicate Redis ready entries atomically.
- Remove intermediate speech-containing audio at source expiry, retaining only the delivered result.
- Kill and reap DeepFilterNet on cancellation as well as timeout.
- Preserve delayed audio and full video duration; copy AAC at remux; measure the complete processing wall time.

## Verification

Local tests cover real FFmpeg audio/video processing, the official DeepFilterNet 0.5.6 AMD64 binary, all presets/formats, delayed audio onset and video packet hashes, duplicate claims, deletion during processing, TTL cleanup, worker locking, crash retry limits, and provider errors/cancellation. Synthetic signals prove mechanics, not listening quality.

Real PostgreSQL migration/claim and Redis queue tests use `TEST_DATABASE_URL` and `TEST_REDIS_URL`. They are skipped if these services are absent; CI supplies isolated services and also builds the DeepFilterNet worker image. The PostgreSQL test creates and drops only its own random schema. The Redis test uses private keys, never `FLUSHDB`.

## Open release gates

1. Live Telegram E2E with Local Bot API: video over 20 MB, voice, reconnect/restart, delete and reprocess. A token/API credentials alone do not prove delivery works.
2. Human blind listening on real Russian and English recordings, including echo, street noise and music. Existing public-corpus results are reported by the earlier implementation; its ignored media files are not available in a new checkout.
3. Add admission limits, concurrent upload limits and disk-space reserve checks before public traffic. Up to 2 GB per upload can exhaust a small VPS quickly.
4. Delivery is at-least-once around the Telegram-send/database-commit boundary. A crash after a successful send can repeat the result. Telegram send operations have no application-supplied idempotency key; do not promise exactly-once delivery.
5. User deletion cannot recall an upload already accepted by Telegram. The Bot API cache is separate from application storage and still needs an explicit operational retention policy.
6. Adopt versioned migrations (Alembic) before shared staging/production. This alpha uses an additive, idempotent initializer; the legacy PostgreSQL test now also upgrades channel/string-ID columns and preserves Telegram IDs.
7. Preview, payments/pricing, S3 and distributed workers remain unfinished. Profile fields for de-essing, reverb and ambience are not fully implemented. Noise/compression control curves and a second denoising stage after AI still need audio calibration; avoid marketing them as measured improvements.
8. Pin container image digests after a successful deployment build; the current local Bot API image tracks `latest`. ARM64 DeepFilterNet checksum is recorded, but its runtime has not been exercised here.

## Free engines

- [DeepFilterNet](https://github.com/Rikorose/DeepFilterNet): open source speech noise suppression (MIT or Apache-2.0 code), CPU-oriented and already integrated. Test it first for a small VPS. [Official releases](https://github.com/Rikorose/DeepFilterNet/releases/tag/v0.5.6).
- [Resemble Enhance](https://github.com/resemble-ai/resemble-enhance): MIT project with denoising and speech restoration; a candidate for a separate benchmark. Do not add its heavier inference dependencies to the bot environment before measuring resource use and quality.

Free self-hosted software removes provider API fees, not compute/storage costs. Neither is a verified drop-in quality replacement for ElevenLabs on this project's recordings. Text-to-speech/voice-cloning alternatives are a different product capability.


## MAX extension and real-speech check

MAX uses its official REST schema through an httpx adapter, avoiding another bot
framework. `Delivery` isolates messenger output from the media worker. PostgreSQL
owns a durable inbox for MAX webhook acceptance; a separate, locked ingress
process handles download/probe and message actions. MAX-only Compose removes the
Telegram deployment dependency. See [MAX.md](MAX.md) for setup and release gates.

Job and analytics ownership now includes the channel; external MAX message IDs
remain strings. For backwards-compatible deployment, physical database columns
named `telegram_chat_id`, `telegram_user_id` and traffic counters retain their
legacy names but hold the channel's IDs/bytes. They must always be interpreted
alongside `processing_jobs.channel`; no cross-channel identity linking is implied.

The paired speech benchmark exposed a 25 ms FFmpeg `afftdn` delay, now compensated
with padding and trimming at 48 kHz. A real chirp regression measures lag and end
of signal. Reference-based metrics and listening examples are documented in
[AUDIO_COMPARISON.md](AUDIO_COMPARISON.md); they do not constitute human ratings.
