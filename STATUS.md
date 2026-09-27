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

Run `python -m voice_enhancer.doctor` to check local prerequisites without printing secrets. On this development machine, the Telegram credentials are set and local FFmpeg/DeepFilterNet binaries work via explicit paths, but Docker and FFmpeg on `PATH` are absent. The public-corpus smoke test has a technical-only report in `media/benchmark_public_v1/technical_report.json`; the complete provider comparison still needs ElevenLabs access and human ratings.

Preview is permitted to be disabled for an early alpha by the specification; it is not implemented yet. Core receipt, selection, processing, and failure events are stored locally, while preview, download, and payment events need their corresponding product flows. User data deletion is available through `/delete_my_data` and deferred media cleanup, but still needs a deployed end-to-end check. Production S3 storage, multi-worker coordination, payments, and real pricing are still outstanding.

An optional GLM-5.3-Flash text-to-profile adapter now works in the offline command with strict field, delta, and profile-bound validation. It does not affect the default audio path. A live API check and Telegram UI integration require a GLM API key and belong to the later natural-language editing phase.
