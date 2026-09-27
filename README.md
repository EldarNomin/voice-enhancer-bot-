# Voice Enhancer Bot

Telegram bot for improving speech in short videos and audio, based on [`SPEC.md`](SPEC.md).

## Current stage

The bot accepts Telegram video, audio, and voice messages, validates metadata, offers four presets, queues processing, and returns a result. PostgreSQL stores job state, Redis moves jobs to the worker, and both services share a media volume. The worker resumes interrupted jobs after restart and removes media after its retention window.

The bot uses Russian when the user's Telegram language is Russian and English otherwise. It saves the language with each job so progress and result messages use the same language. On startup it sets the default English Telegram profile and the Russian-localized name and descriptions. The chosen username is `@voice_enhancer_studio_bot`; create this account in BotFather before starting the service.

The default processing engine is a local FFmpeg DSP baseline. An optional ElevenLabs Voice Isolator adapter is available for listening tests. The production provider has not been selected; S3 storage, A/B preview, billing, and multi-worker coordination remain to be implemented.

## Local setup

Requirements: Docker Compose, a Telegram bot token, and Telegram API ID/hash for the local Bot API server. Set `BOT_TOKEN`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH` in `.env`. API ID/hash come from [my.telegram.org](https://my.telegram.org).

```bash
cp .env.example .env
docker compose up --build
```

If this bot token was previously used with Telegram's cloud Bot API, call its `logOut` method on that server before starting it against the local API server. The services share the Telegram data volume to read downloaded files and a media volume for worker input/output. Run one worker instance at this stage.

To compare the ElevenLabs isolation stage, set `ENHANCEMENT_PROVIDER=elevenlabs` and `ELEVENLABS_API_KEY` in `.env`. This sends the uploaded audio to ElevenLabs and may incur provider charges. Keep `ENHANCEMENT_PROVIDER=ffmpeg` for the local baseline. Selection for users should follow the blind listening benchmark in `SPEC.md`.

Open `http://localhost:8080/health` to check the API container. Bot and worker require the local Telegram API server and working credentials; a healthy API endpoint alone does not verify Telegram delivery.

Run tests locally:

```bash
python -m pip install -e '.[dev]'
pytest
```

FFmpeg applies a conservative DSP chain for the current vertical slice. This is a development baseline, not an AI speech enhancement provider or a substitute for the blind provider benchmark required by the specification.

Files waiting for a preset expire after 30 minutes; completed originals expire after 24 hours and completed results after 72 hours.

## Project layout

```text
src/voice_enhancer/
  domain/          profiles and processing job state machine
  application/     media validation and processing use case
  infrastructure/  FFmpeg and provider adapters
  api/             health endpoint
  bot.py           Telegram input and preset selection
  worker.py        queued processing and result delivery
tests/
```
