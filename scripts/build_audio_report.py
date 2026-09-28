"""Build a self-contained listening page and a measured Markdown report."""

import base64
import csv
import html
import json
import random
import statistics
import subprocess
from pathlib import Path

ROOT = Path("artifacts/audio-comparison")
VARIANTS = ["input", "ffmpeg", "deepfilter", "deepfilter_dsp", "gtcrn", "gtcrn_dsp"]
LABELS = {
    "input": "Исходный звук",
    "ffmpeg": "FFmpeg / Natural",
    "deepfilter": "DeepFilterNet без DSP",
    "deepfilter_dsp": "DeepFilterNet + Natural",
    "gtcrn": "GTCRN без DSP",
    "gtcrn_dsp": "GTCRN + Natural",
}
FILES = {
    "input": "noisy.wav",
    "ffmpeg": "ffmpeg.m4a",
    "deepfilter": "deepfilter.wav",
    "deepfilter_dsp": "deepfilter_dsp.m4a",
    "gtcrn": "gtcrn.wav",
    "gtcrn_dsp": "gtcrn_dsp.m4a",
}
CONDITIONS = {
    "real_noise": "EN / реальный шум (24)",
    "noise5": "RU / шум 5 dB (6)",
    "noise15": "RU / шум 15 dB (6)",
    "echo": "RU / искусственное эхо (6)",
    "clean": "RU / чистая речь (6)",
}


def build():
    metrics = json.loads((ROOT / "metrics.json").read_text())
    metrics += json.loads((ROOT / "metrics-gtcrn.json").read_text())
    assert len(metrics) == 48 * 6, "Wait for both benchmarks to finish"
    destination = Path("docs/audio-comparison")
    destination.mkdir(parents=True, exist_ok=True)
    fields = [key for key in metrics[0] if key != "startup_seconds"]
    with (destination / "metrics.csv").open("w") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(metrics)
    for name in ["voicebank-manifest.json", "fleurs-manifest.json", "russian-manifest.json"]:
        (destination / name).write_bytes((ROOT / name).read_bytes())
    tables = {}
    for metric in ["stoi", "si_sdr_db"]:
        table = "| Условие | " + " | ".join(LABELS[v] for v in VARIANTS) + " |\n"
        table += "|---|" + "---:|" * len(VARIANTS) + "\n"
        for condition, title in CONDITIONS.items():
            values = []
            for variant in VARIANTS:
                rows = [
                    r for r in metrics if r["condition"] == condition and r["variant"] == variant
                ]
                value = statistics.mean(r[metric] for r in rows)
                values.append(
                    "—"
                    if condition == "clean" and variant == "input" and metric == "si_sdr_db"
                    else f"{value:.3f}"
                    if metric == "stoi"
                    else f"{value:.2f}"
                )
            table += "| " + title + " | " + " | ".join(values) + " |\n"
        tables[metric] = table
    report = (
        """# Проверка звука и решение по ElevenLabs — 2026-09-27

**Платную подписку ElevenLabs сейчас не покупать.** Локальные движки уже можно
использовать для пилота. По этой небольшой выборке нельзя назвать ни один из них
универсальным победителем или доказанной заменой ElevenLabs.

- DeepFilterNet лучше остальных локальных кандидатов подавляет шум в английских
  парах по SI-SDR, но на части русских записей снижает STOI, особенно при слабом шуме.
- GTCRN значительно легче, сохраняет больше разборчивости в некоторых русских
  условиях, но ограничен моно 16 кГц. Он добавлен как опциональный провайдер бота.
- FFmpeg DSP меняет тембр, динамику и громкость. SI-SDR штрафует такие изменения;
  падение этой метрики само по себе не доказывает неприятный звук. Однако добавление
  DSP после AI в этой выборке снижает и STOI. Калибровка пресетов и прослушивание нужны.
- Для чистой речи агрессивная очистка не оправдана автоматически. Простое
  шумоподавление также не решило задачу эха. Не обещать «студию из любого исходника».
- Настройки по умолчанию не переключены по метрикам без прослушивания.
- В одном очень тихом русском исходнике (ru6, peak −31.25 dBFS) DeepFilterNet
  почти полностью подавил голос. После этого измерения в общий pipeline добавлена
  защита: если AI теряет более 35 dB пикового уровня при входном пике выше −75 dBFS, бот возвращается
  к FFmpeg от исходника. Таблицы и аудио ниже сохраняют результаты **до этой защиты**,
  чтобы дефект был виден. Это консервативный контроль потери сигнала, не оценка речи.

## Данные и метод

48 условий, 351.05 секунды (5.851 минуты): 24 равномерно выбранные пары
VoiceBank-DEMAND-16k test (оба тестовых диктора), 6 записей Google FLEURS ru_ru test
(4 female / 2 male по metadata), каждая в четырёх условиях: исходная, шум 5 dB,
шум 15 dB, искусственное эхо. Это **30 исходных записей**, не 48 независимых дикторов.
Русские шесть файлов — первые WAV в архиве, а не репрезентативная выборка.

Шум для русских смесей = noisy − clean из лицензированных английских пар,
повторён до нужной длины и масштабирован по RMS всей записи. SNR не измерен по
активным речевым участкам. Эхо: прямой сигнал + задержки 80/190/340/500 ms с
коэффициентами 0.45/0.28/0.16/0.08. Это искусственная стресс-проверка, не реальная комната.

Все сравнения сведены к моно 16 кГц. По ним нельзя оценить высокочастотную
естественность 48 кГц или стереобазу. STOI (0–1, выше лучше) оценивает разборчивость;
SI-SDR (dB, выше лучше) — сходство с чистым сигналом с поправкой на общий gain.
Для чистого входа SI-SDR формально неограничен; его не усредняем с шумовыми случаями.
Нет ручных оценок, MOS, ASR/WER или проверки всех реальных акустических условий.

Задержка не подгонялась индивидуально по эталону. Компенсация DeepFilter включена;
найденный при проверке сдвиг FFmpeg afftdn на 25 ms исправлен в общем движке.
При оценке сравнивается общая длина сигналов; хвост AAC до одного кадра учитывается
отдельно в `duration_drift_ms`. Сырые значения и SHA256 источников в `audio-comparison/`.

## STOI

"""
        + tables["stoi"]
        + """
## SI-SDR, dB

"""
        + tables["si_sdr_db"]
        + """
## Скорость и воспроизводимость

DeepFilterNet 0.5.6, официальный AMD64 CLI, запускается заново для каждого файла;
GTCRN — sherpa-onnx 1.13.8, CPU, один inference thread, модель загружена один раз на
пакет. Поэтому цифры GTCRN ниже — тёплая пакетная оценка, не полная задержка бота:
бот запускает изолированный процесс на задание. FFmpeg extraction, сетевые transfer
и очередь в этих временах не учтены. Во время теста были конкурирующие процессы.

"""
    )
    report += "| Вариант | Σ compute / Σ audio |\n|---|---:|\n"
    for variant in VARIANTS[1:]:
        rows = [r for r in metrics if r["variant"] == variant]
        rtf = sum(r["compute_seconds"] for r in rows) / sum(r["seconds"] for r in rows)
        report += f"| {LABELS[variant]} | {rtf:.3f} |\n"
    report += """
Отдельная проверка DeepFilter на 60-секундной стереозаписи: 68.11 s, RTF 1.135.
Это характеристика данной среды, не гарантия скорости на VPS. GTCRN возвращает
16 кГц; последующий апсемплинг в 48 кГц не восстанавливает потерянную полосу.

```sh
pip install -c requirements.lock -e '.[benchmark,gtcrn]'
python scripts/install_deepfilter.py /tmp/deep-filter
python scripts/install_gtcrn.py /tmp/gtcrn_simple.onnx
python scripts/fetch_comparison_audio.py
python scripts/compare_audio.py --deepfilter-bin /tmp/deep-filter
python scripts/compare_gtcrn.py --model /tmp/gtcrn_simple.onnx
python scripts/build_audio_report.py
```

Для включения GTCRN в боте: `docker compose -f docker-compose.yml -f compose.gtcrn.yml
up -d --build` (одной строкой). Для MAX добавить `--profile max`; вариант только MAX
описан в [MAX.md](MAX.md). Альтернатива — `compose.deepfilter.yml`. Это выбор
оператора для пилота, автоматического угадывания оптимального движка пока нет.

## ElevenLabs: когда платить

На проверенной странице ElevenAPI: Voice Isolator **$0.12/min**, расчёты API в USD,
доступен pay-as-you-go. Старые 1000 credits/min относятся к другой схеме тарификации;
не смешивать её с актуальными API USD. Вся эта выборка стоит ориентировочно
**$0.70** за один проход ($0.12 × 351.05 / 60), без налогов, округления, повторов и
условий аккаунта. Это стоимость использования, не обещание минимального платежа.

Практический следующий шаг — тот же небольшой A/B тест ElevenLabs на сложных
русских случаях и эхо, затем прослушивание с выровненной громкостью. Покупать
ежемесячный план заранее не требуется; необходимость и доступность оплаты надо
проверить в конкретном аккаунте. API-ключа здесь нет, запросов к ElevenLabs не было,
победителя по качеству против ElevenLabs объявить нельзя.

Resemble Enhance (MIT) также рассмотрен. Публичная официальная демоверсия ответила
ZeroGPU quota exhausted (0 s available), поэтому её аудио и оценки не включены.
Обход квоты не выполнялся. Локальный GPU-тест остаётся отдельным вариантом.

## Источники, лицензии и версии

- VoiceBank-DEMAND: C. Valentini-Botinhao, «Noisy speech database for training speech
  enhancement algorithms and TTS models», University of Edinburgh, CC BY 4.0:
  https://datashare.ed.ac.uk/handle/10283/2791 . Использован 16 кГц mirror
  https://huggingface.co/datasets/JacobLinCool/VoiceBank-DEMAND-16k . Изменения:
  ресемплинг, выделение остаточного шума, смеси и обработка, описанные выше.
- Google FLEURS: Alexis Conneau et al., «FLEURS: Few-shot Learning Evaluation of
  Universal Representations of Speech», 2022, CC BY 4.0:
  https://huggingface.co/datasets/google/fleurs . Изменения: mono/16k, шум/эхо/DSP.
- Лицензия записей: https://creativecommons.org/licenses/by/4.0/ . Авторы не
  подтверждают выводы и не рекламируют этот бот.
- DeepFilterNet: https://github.com/Rikorose/DeepFilterNet , CLI 0.5.6,
  SHA256 `70775e251eee44c0f2451a1e833326cf8bcbbe304d3e7cd12851e6fce72ef7da`.
- GTCRN: https://github.com/Xiaobin-Rong/gtcrn (MIT, Rong Xiaobin, 2024);
  официальный export https://github.com/k2-fsa/sherpa-onnx/releases/tag/speech-enhancement-models ,
  `gtcrn_simple.onnx`, SHA256 `e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534`.
  Лицензия сохранена в `licenses/GTCRN-MIT.txt`; контрольная сумма проверяется при запуске.
- Resemble: https://github.com/resemble-ai/resemble-enhance ;
  demo https://huggingface.co/spaces/ResembleAI/resemble-enhance .
- ElevenAPI: https://elevenlabs.io/pricing/api ;
  https://elevenlabs.io/blog/weve-lowered-api-agents-pricing-and-introduced-pay-as-you-go .
  Цены проверены 2026-09-27, перед оплатой перепроверить условия аккаунта.
"""
    Path("docs/AUDIO_COMPARISON.md").write_text(report)
    sample_ids = [
        "p232_151",
        "p257_075",
        "ru1_noise5",
        "ru2_noise15",
        "ru3_echo",
        "ru4_clean",
        "ru5_noise5",
        "ru6_echo",
    ]
    cards = []
    rng = random.Random(20260927)
    for identifier in sample_ids:
        variants = VARIANTS.copy()
        rng.shuffle(variants)
        players, mapping = [], []
        for index, variant in enumerate(variants):
            letter = chr(65 + index)
            source = ROOT / identifier / FILES[variant]
            normalized = ROOT / "preview.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-af",
                    "loudnorm=I=-20:TP=-2:LRA=11",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(normalized),
                ],
                check=True,
            )
            data = base64.b64encode(normalized.read_bytes()).decode()
            players.append(
                f'<div><b>Вариант {letter}</b><audio controls preload="none" '
                f'src="data:audio/wav;base64,{data}"></audio></div>'
            )
            mapping.append(f"{letter} — {LABELS[variant]}")
        cards.append(
            f'<section><h2>{identifier}</h2><div class="players">'
            + "".join(players)
            + "</div><details><summary>Раскрыть методы</summary><p>"
            + "<br>".join(mapping)
            + "</p></details></section>"
        )
    normalized.unlink(missing_ok=True)
    page = (
        """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Проверка голоса — 48 условий</title><style>
body{max-width:1080px;margin:auto;padding:32px 20px;background:#f5f7fb;color:#172238;font:16px/1.6 system-ui}
h1{font-size:38px;line-height:1.2}h2{font-size:23px}section{background:white;padding:24px;border-radius:16px;margin:20px 0;border:1px solid #dce3ee}
.players{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}audio{display:block;width:100%;margin-top:8px}
summary{cursor:pointer;color:#254db0;margin-top:20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 system-ui} .tag{color:#456289;font-size:14px}
</style><body><p class="tag">VOICE ENHANCER · TELEGRAM + MAX · 27 СЕНТЯБРЯ 2026</p>
<h1>Нужно ли платить за очистку голоса?</h1>
<p><b>Пока нет оснований покупать подписку ElevenLabs.</b> Ниже — реальные открытые записи и результаты трёх локальных движков. Одного универсального победителя не выявлено.</p>
<p>Сначала послушайте варианты, затем раскройте названия. Громкость выровнена до −20 LUFS, файлы для прослушивания — WAV, моно 16 кГц. Сравнивайте сохранность слов, шум, металлический тембр и паузы. При выравнивании громкости шум в паузах может стать заметнее.</p>
<p>48 условий на 30 исходных записях. Здесь 8 примеров; полные измерения и ограничения — в отчёте ниже. Автоматические метрики не заменяют человеческое прослушивание. ElevenLabs и Resemble в аудиосравнении отсутствуют.</p>
"""
        + "".join(cards)
        + "<section><h2>Полный отчёт</h2><pre>"
        + html.escape(report)
        + "</pre></section></body></html>"
    )
    (ROOT / "audio-comparison.html").write_text(page)
    print(f"Created listening page: {len(page.encode()) / 1024**2:.1f} MiB")


if __name__ == "__main__":
    build()
