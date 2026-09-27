# Voice Enhancer Bot

Telegram bot for improving speech in short videos and audio, based on [`SPEC.md`](SPEC.md).

## Current stage

The first vertical slice accepts Telegram video, audio, and voice messages, validates media metadata, offers four presets, processes the audio, and returns a result. The domain model, FFmpeg adapter, API health route, Docker setup, and unit tests are in place.

The bot currently keeps pending uploads in process memory and uses a local FFmpeg DSP baseline. Durable jobs, Redis queueing, database persistence, S3 storage, Local Telegram Bot API Server, and a benchmark-selected AI speech enhancement provider remain to be implemented.

## Local setup

Requirements: Python 3.12+, FFmpeg/ffprobe, Docker Compose.

```bash
cp .env.example .env
docker compose up --build
```

Run tests locally:

```bash
python -m pip install -e '.[dev]'
pytest
```

FFmpeg applies a conservative DSP chain for the current vertical slice. This is a development baseline, not an AI speech enhancement provider or a substitute for the blind provider benchmark required by the specification.

## Project layout

```text
src/voice_enhancer/
  domain/          profiles and processing job state machine
  application/     media validation and processing use case
  infrastructure/  FFmpeg and provider adapters
  api/             health endpoint
tests/
```
