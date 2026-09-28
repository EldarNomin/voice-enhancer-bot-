# Voice Enhancer Bot

Telegram bot for improving speech in short videos and audio, based on [`SPEC.md`](SPEC.md).

Progress against the MVP acceptance criteria is tracked in [`STATUS.md`](STATUS.md).

## Current stage

The bot accepts Telegram video, audio, and voice messages, validates metadata, offers four presets, queues processing, and returns a result. After delivery the user can choose another style while the original remains within its retention window. PostgreSQL stores job state, deduplicated MVP events, and measured per-job resource use. Redis moves jobs to the worker, and both services share a media volume. The worker resumes interrupted jobs after restart and removes media after its retention window. Before delivery it checks duration, decodability, audio sample rate and sample peak, plus video stream properties for video jobs. Monetary totals remain unset until actual provider and infrastructure rates are configured.

The bot uses Russian when the user's Telegram language is Russian and English otherwise. It saves the language with each job so progress and result messages use the same language. On startup it sets the default English Telegram profile and the Russian-localized name and descriptions. The chosen username is `@voice_enhancer_studio_bot`; create this account in BotFather before starting the service.

Users can send `/delete_my_data` to remove their job history and request deletion of stored files. An in-use file is retried by the cleanup worker after the active job stops. Telegram messages already delivered to the chat are not removed by this command.

The default processing engine is a local FFmpeg DSP baseline. Optional ElevenLabs Voice Isolator and DeepFilterNet adapters are available for listening tests. The production provider has not been selected; S3 storage, A/B preview, billing, and multi-worker coordination remain to be implemented.

## Local setup

Requirements: Docker Compose, a Telegram bot token, and Telegram API ID/hash for the local Bot API server. Set `BOT_TOKEN`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH` in `.env`. API ID/hash come from [my.telegram.org](https://my.telegram.org). Other settings and defaults are listed in `src/voice_enhancer/config.py`.

```bash
cp .env.example .env
# Fill in Telegram credentials, then:
docker compose up --build
```

If this bot token was previously used with Telegram's cloud Bot API, call its `logOut` method on that server before starting it against the local API server. The services share the Telegram data volume to read downloaded files and a media volume for worker input/output. Run one worker instance at this stage.

To compare the ElevenLabs isolation stage, set `ENHANCEMENT_PROVIDER=elevenlabs` and `ELEVENLABS_API_KEY` in `.env`. This sends the uploaded audio to ElevenLabs and may incur provider charges. Temporary HTTP errors are retried with bounded backoff. Keep `ENHANCEMENT_PROVIDER=ffmpeg` for the local baseline. Selection for users should follow the blind listening benchmark in `SPEC.md`.

For a local AI engine without per-minute API fees, use the optional DeepFilterNet worker image:

```bash
docker compose -f docker-compose.yml -f compose.deepfilter.yml up --build -d
```

This selects DeepFilterNet only for the worker and installs the official 0.5.6 binary with a pinned SHA-256. Linux AMD64 and ARM64 artifacts are configured; AMD64 has been exercised locally, ARM64 still needs runtime testing. The build needs GitHub access. No GLM or ElevenLabs key is required. Hosting and CPU time still cost money. The standard Compose command retains the FFmpeg baseline.

See [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md) for design decisions, verification and the remaining release gates. Runtime Python dependencies are pinned in `requirements.lock`; update deliberately with `uv pip compile pyproject.toml -o requirements.lock` and rerun tests.

## Offline listening benchmark

This benchmark does not require Telegram credentials. Install FFmpeg and ffprobe locally, and collect at least 20 licensed or self-recorded clips in `media/corpus/` covering the recording conditions in `SPEC.md`. Media under `media/` is ignored by Git. For the local comparison, download the official [DeepFilterNet binary](https://github.com/Rikorose/DeepFilterNet/releases) and put `deep-filter` on `PATH`, or set `DEEPFILTER_BIN` to its executable path. The supported adapter targets the Rust binary; the Python CLI is not verified. On supported Linux machines, `python scripts/install_deepfilter.py /your/path/deep-filter` installs the pinned version. The optional Docker build above includes this binary.

To reproduce the first public-corpus smoke test, run `python scripts/fetch_public_corpus.py`. It fetches 20 noisy-speech rows from the [Speech and Noise Dataset](https://huggingface.co/datasets/haydarkadioglu/speech-noise-dataset), whose card declares CC0, and records row indices and SHA-256 hashes in `media/corpus/corpus_manifest.json`. This is a preliminary English audio-only sample without verified labels for every SPEC recording condition; review source rights before redistributing the files or using them for commercial training. The current prepared local test is in `media/benchmark_public_v1/`.

To process a single local file without Telegram, run:

```bash
python -m voice_enhancer.offline input.mp4 output.mp4 --preset studio --provider ffmpeg
```

For audio, use an `.m4a` output path. The command leaves the input untouched, refuses to overwrite an existing output, and prints media quality checks.

An optional phase-2 text adjustment can be exercised offline by setting `GLM_API_KEY` in `.env` and adding `--instruction "Сделай голос мягче"` to the command above. It uses [Z.ai GLM-5.3-Flash](https://docs.z.ai/guides/vlm/glm-5.3-flash) only to propose bounded relative changes to `warmth`, `presence`, `noise_reduction`, and `compression`; the application validates every field and value before FFmpeg runs. Media is not sent to GLM. The regular bot path does not call GLM, and this offline integration has not been live-tested without an API key. Other profile controls are intentionally excluded until their DSP behavior is implemented and benchmarked.

```bash
python -m voice_enhancer.benchmark prepare --corpus media/corpus --output media/benchmark --providers ffmpeg deepfilter --preset studio
```

Add `elevenlabs` to `--providers` only when `ELEVENLABS_API_KEY` is set and you intend to pay for API processing. The runner produces normalized, anonymously labeled listening files in `samples/`, a `ratings.csv` template, and `mapping.private.json`. Give listeners only `samples/` and `ratings.csv`; keep the mapping hidden until scoring is complete. Each listener fills all seven scores from 1 to 5 and a unique `reviewer` value for each row. Aggregate the scores with:

```bash
python -m voice_enhancer.benchmark summarize --output media/benchmark
```

The resulting `report.json` includes average scores and paired win/tie/loss counts against the original. Preparation also checks that outputs decode, retain a 48 kHz audio stream, stay within 250 ms of source duration, avoid sample peak clipping, and preserve video codec, dimensions, and frame rate. These checks do not measure perceptual quality or guarantee lip sync. Human ratings, the optional ElevenLabs comparison, and more diverse recording conditions are still needed before selecting a production provider.

Run `python -m voice_enhancer.benchmark inspect --output media/benchmark_public_v1` to write a technical-only `technical_report.json` before any listening ratings are available.

Open `http://localhost:8080/health` to check the API container. Bot and worker require the local Telegram API server and working credentials; a healthy API endpoint alone does not verify Telegram delivery.

Run tests locally:

```bash
python -m pip install -c requirements.lock -e '.[dev]'
pytest
```

Check local prerequisites without printing secrets:

```bash
python -m voice_enhancer.doctor
```

The offline and benchmark commands accept `--ffmpeg-bin`, `--ffprobe-bin`, and `--deepfilter-bin` when executables are installed outside `PATH`.

FFmpeg applies a conservative DSP chain for the current vertical slice. This is a development baseline, not an AI speech enhancement provider or a substitute for the blind provider benchmark required by the specification.

Files waiting for a preset expire after 30 minutes; completed originals expire after 24 hours and completed results after 72 hours.

Intermediate WAV/provider files are removed with the original at 24 hours; only the result is kept for 72 hours. This applies to the application media volume. Telegram's separate local Bot API cache and copies already delivered in chats have a separate lifecycle; `/delete_my_data` does not erase Telegram messages or that cache. Cleanup retries filesystem failures. Run only one worker on one shared Linux filesystem; an OS lock enforces this for the same media volume. This is not a distributed multi-host worker design.

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


## MAX (РФ)

Поддерживаются Telegram и MAX с общей очередью и движком обработки. MAX — опциональный
канал: webhook → PostgreSQL inbox → обработчик сообщений → общая очередь → media worker.
Токены и права пользователей разделены по каналам. Есть вариант запуска только MAX.
Подробная настройка, ограничения и проверка: [docs/MAX.md](docs/MAX.md).

Сравнение локальных движков на открытой речи: [docs/AUDIO_COMPARISON.md](docs/AUDIO_COMPARISON.md).
