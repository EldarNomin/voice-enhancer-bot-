# Implementation status

The repository is an offline-testable alpha candidate, not a verified production bot. See `SPEC.md` for the acceptance criteria.

| SPEC criterion | Current evidence | Remaining check |
| --- | --- | --- |
| MP4 in → MP4 out | Real FFmpeg integration tests exercise remux for MP4, MOV, MKV, and WEBM. | Run a Telegram end-to-end test with the local Bot API server. |
| Four presets | All four pass the real FFmpeg media test. | Compare sound quality on real speech. |
| Audio and voice input | Bot handlers and audio format tests cover WAV, MP3, M4A, AAC, OGG, OPUS, and FLAC. | Run a Telegram voice-message end-to-end test. |
| Visually identical video | Technical checks compare codec, frame rate, dimensions, and require stream copy. | Inspect representative real clips. |
| A/V sync | Output audio and video duration difference is limited to 250 ms. | Measure actual alignment on a licensed test corpus. |
| At least 95% valid media success | All 20 public noisy-speech clips passed local FFmpeg and DeepFilterNet processing and technical checks (40/40 variants). | Repeat on diverse real audio and video, including all SPEC conditions. |
| Duplicate update safety | Database unique constraint and worker claim tests pass. | Check against live Telegram updates. |
| Safe provider error | Bot shows a generic failure; adapter avoids response bodies and credentials in errors. | Run a live provider failure smoke test. |
| Job cost | One row per job records provider, compute seconds, and input/output/storage bytes. | Configure actual rates to calculate USD totals. |
| TTL cleanup | Source, result, and unselected upload retention are unit-tested. | Verify on deployed storage. |
| Benchmark report | 20 public clips produced 60 blinded samples and a ratings template in `media/benchmark_public_v1/`; source row IDs and hashes are recorded. | Add ElevenLabs when keyed, cover missing recording conditions, and collect listening ratings. |
| Better than original | The summary counts paired wins/ties/losses against the original. | Human blind listening is required to make this claim. |

Run `python -m voice_enhancer.doctor` to check prerequisites on the machine where the bot will run. Ignored media, local executable paths and credentials are machine-specific and are not restored by cloning this repository. The earlier public-corpus smoke test was reported in `media/benchmark_public_v1/technical_report.json`; the complete provider comparison still needs human ratings and, optionally, ElevenLabs access.

Preview is permitted to be disabled for an early alpha by the specification; it is not implemented yet. Core receipt, selection, processing, and failure events are stored locally, while preview, download, and payment events need their corresponding product flows. User data deletion is available through `/delete_my_data` and deferred media cleanup, but still needs a deployed end-to-end check. Production S3 storage, multi-worker coordination, payments, and real pricing are still outstanding.

An optional GLM-5.3-Flash text-to-profile adapter now works in the offline command with strict field, delta, and profile-bound validation. It does not affect the default audio path. A live API check and Telegram UI integration require a GLM API key and belong to the later natural-language editing phase.

## Architecture and reliability pass

See [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md). Local verification: **71 passed, 2 skipped** with real FFmpeg and the official DeepFilterNet 0.5.6 AMD64 binary; skipped cases require live PostgreSQL/Redis. Ruff, dependency consistency and YAML parsing passed. The DeepFilterNet installer checksum was exercised. Docker/service tests are configured in GitHub Actions; their success must be checked separately.

Fixed local API routing, dependency pinning, single-worker enforcement, persistent retry limits, queue deduplication/reconciliation, intermediate media retention, provider cancellation, complete processing-time measurement, and A/V alignment for delayed audio. A real delayed-audio test checks signal onset and identical compressed video hashes. The optional DeepFilterNet Compose override removes the need for an external enhancement API for initial tests. Actual Telegram delivery and perceptual quality are still release gates.


## Telegram + MAX and measured audio comparison (2026-09-28)

MAX is implemented as an optional official REST/webhook channel with durable inbox,
channel-scoped ownership, presets, reprocessing and deletion. The worker delivers to
either messenger through an adapter; `compose.max-only.yml` supports MAX without
Telegram credentials. Setup and live-test prerequisites: [docs/MAX.md](docs/MAX.md).
No live MAX session has been tested: MAX token and HTTPS deployment are not available here.

A new reproducible comparison covers 48 conditions from 30 open recordings, with
English paired noisy/clean speech and Russian clean/noise/echo variants. FFmpeg,
DeepFilterNet and GTCRN are measured separately from optional preset DSP. Source
hashes, raw metrics, licensing and payment recommendation are in
[docs/AUDIO_COMPARISON.md](docs/AUDIO_COMPARISON.md). The benchmark exposed and fixed
25 ms DSP latency. A near-silent neural-output guard now falls back to the original
signal's FFmpeg processing; the report intentionally retains pre-guard measurements.
GTCRN is an optional CPU provider with a checksum-pinned model and separate Docker
target, with the explicit mono/16 kHz quality limitation. No automatic engine switch.

Do not buy an ElevenLabs monthly plan yet. The same corpus allows a small metered
comparison later. Without an ElevenLabs key or human listening ratings, no universal
quality winner is claimed. Resemble's public demo rejected inference for exhausted
GPU quota; it has no result in the comparison. GLM payment is not a dependency for
preset-based processing in either messenger.
