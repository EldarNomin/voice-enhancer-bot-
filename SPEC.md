# Техническое задание — Voice Enhancer Bot

**Версия:** 0.1  
**Статус:** Draft for implementation  
**Канал:** Telegram Bot  
**Основная платформа:** Telegram  
**Язык MVP:** русский и английский; выбор интерфейса по языку Telegram

---

## 1. Цель продукта

Создать Telegram-бота, который улучшает голос в видео, аудио и голосовых сообщениях так, чтобы запись воспринималась как сделанная через более качественный микрофон и обработанная для публикации в Reels / TikTok / Shorts / Telegram.

Пользователь не должен регистрироваться на внешнем сайте, скачивать отдельное приложение или разбираться в аудиообработке.

### Основной value proposition

> «Отправь видео → получи тот же ролик с профессионально обработанным голосом».

---

## 2. Целевая аудитория

Приоритетные сегменты MVP:

1. UGC-креаторы.
2. Авторы Reels / TikTok / Shorts.
3. Эксперты и блогеры, записывающие talking-head видео на телефон.
4. Владельцы Telegram-каналов.
5. Подкастеры и авторы интервью.
6. Малый бизнес, снимающий контент без профессионального микрофона.

Основной первый use case: **вертикальный talking-head ролик, снятый на смартфон**.

---

## 3. Проблема пользователя

Типичные дефекты мобильной записи:

- фоновый шум;
- шум улицы / вентиляции / автомобиля;
- лёгкое или сильное эхо помещения;
- слишком «далёкий» голос;
- слабая плотность и presence;
- резкие сибилянты;
- скачущая громкость;
- перегруз отдельных фрагментов;
- голос плохо читается из динамика телефона;
- музыка/ambience конкурируют с речью.

Пользователь обычно не знает терминов `EQ`, `compressor`, `de-esser`, `LUFS` и не должен знать их для использования продукта.

---

## 4. Scope MVP

### 4.1 Входные данные

Бот должен принимать:

- video;
- audio;
- voice message;
- document, если расширение относится к поддерживаемым audio/video форматам.

Целевые форматы MVP:

**Video**
- MP4
- MOV
- WEBM
- MKV

**Audio**
- WAV
- MP3
- M4A
- AAC
- OPUS / OGG
- FLAC

### 4.2 Ограничения MVP

На первом production-релизе:

- длительность одного файла: до 10 минут;
- один основной голос;
- русский и английский голос должны обрабатываться одинаково, так как pipeline не должен зависеть от ASR;
- если в исходнике присутствует музыка, pipeline не обязан идеально разделять вокал и музыку;
- многодорожечный монтаж не входит в MVP.

Архитектура должна позволять увеличить ограничения без изменения domain-модели.

---

## 5. User Flow

### 5.1 Первый запуск

Команда `/start`.

Бот отвечает:

> 🎙 Отправь мне видео, аудио или голосовое.  
> Я очищу голос, уберу лишний шум и сделаю звучание плотнее и ближе к профессиональному микрофону.

Кнопки:

- `Как это работает`
- `Тарифы` — скрыть до включения монетизации или показывать «скоро»

### 5.2 Загрузка файла

После получения медиа:

1. Валидировать формат и размер.
2. Определить длительность через `ffprobe`.
3. Создать `Job`.
4. Ответить:

> Файл получен. Как обработать голос?

Кнопки:

- `🌿 Natural`
- `🎙 Studio`
- `📱 Reels`
- `🎧 Podcast`

### 5.3 Описание пресетов

#### Natural

Цель: убрать явный шум и слегка выровнять голос без ощущения «нейросетевой» обработки.

#### Studio

Цель: максимально универсальный «дорогой» и близкий голос.

#### Reels

Цель: голос хорошо читается из динамика телефона, более яркий presence, стабильная громкость.

#### Podcast

Цель: плотный, ровный, спокойный голос с мягкой компрессией.

### 5.4 Preview

После выбора preset система должна уметь создать короткий A/B preview.

Рекомендация:

- автоматически выбрать 6–12 секунд с речью;
- отправить оригинальный фрагмент;
- отправить обработанный фрагмент;
- предложить обработать весь файл.

Кнопки:

- `✨ Обработать весь файл`
- `🎚 Другой стиль`
- `Отмена`

На раннем alpha-релизе preview может быть отключён feature flag, если слишком увеличивает себестоимость.

### 5.5 Полная обработка

Статусы, которые пользователь может видеть:

1. `Файл принят`
2. `Обрабатываю голос…`
3. `Собираю видео…`
4. `Готово`

Не отправлять пользователю технические названия моделей и внутренних стадий.

### 5.6 Результат

Для видео:

- вернуть видео с улучшенной audio track;
- не менять разрешение, FPS и видеокодек без необходимости;
- по возможности использовать remux/copy video stream;
- сохранить A/V sync.

Для audio/voice:

- вернуть audio-файл;
- для voice-message дополнительно можно вернуть Telegram voice format в Phase 2.

Кнопки результата:

- `🌿 Сделать естественнее`
- `🔥 Сделать плотнее`
- `🔁 Другой preset`
- `➕ Новый файл`

Повторная обработка должна использовать сохранённый оригинал, а не предыдущий обработанный результат, чтобы не накапливать артефакты.

---

## 6. Audio Processing Pipeline

### 6.1 Общий pipeline

```text
input media
   ↓
ffprobe metadata
   ↓
extract / normalize working audio (48 kHz)
   ↓
speech enhancement provider
   ↓
DSP post-processing
   ├── high-pass / rumble cleanup
   ├── corrective EQ
   ├── de-esser
   ├── compressor
   ├── presence / warmth EQ
   ├── loudness normalization
   └── true-peak limiter
   ↓
optional ambience remix
   ↓
quality checks
   ↓
remux with original video
   ↓
output media
```

### 6.2 Provider abstraction

Core code не должен зависеть от конкретного AI-провайдера.

Интерфейс:

```python
class SpeechEnhancementProvider(Protocol):
    async def enhance(
        self,
        input_path: Path,
        *,
        profile: ProcessingProfile,
    ) -> EnhancementResult:
        ...
```

Первоначальные кандидаты:

1. `ElevenLabsVoiceIsolatorProvider`
2. `DeepFilterNetProvider`
3. `ClearerVoiceProvider` — benchmark / research

### 6.3 Стратегия выбора provider

До начала полноценной разработки провести blind A/B benchmark.

Набор не менее 20 клипов:

- тихая комната;
- комната с эхо;
- кухня;
- автомобиль;
- улица;
- ветер;
- кондиционер/вентиляция;
- кафе;
- тихая фоновая музыка;
- громкая фоновая музыка;
- мужской/женский голос;
- близкое/далёкое расположение телефона.

Для каждого клипа сравнить минимум:

- ElevenLabs + DSP;
- DeepFilterNet + DSP;
- альтернативный local pipeline.

Оценки 1–5:

- шумоподавление;
- естественность;
- echo/reverb handling;
- плотность;
- intelligibility;
- артефакты;
- общее предпочтение.

Победитель выбирается **по человеческому blind listening**, а не по названию модели.

---

## 7. Processing Profiles

Domain-model:

```json
{
  "preset": "reels",
  "enhancement_strength": 0.75,
  "ambience_retention": 0.15,
  "noise_reduction": 0.85,
  "de_reverb": 0.65,
  "presence": 0.7,
  "warmth": 0.35,
  "compression": 0.6,
  "de_esser": 0.5,
  "target_lufs": -14,
  "true_peak_db": -1.0
}
```

Точные диапазоны должны быть определены по результатам audio benchmark.

Важно: если внешний provider не поддерживает конкретные параметры, adapter отображает `ProcessingProfile` на доступные функции и DSP-stage.

---

## 8. GLM-5.3 Flash

GLM-5.3 Flash **не должен находиться в критическом audio path MVP**.

Phase 2 feature:

Пользователь пишет:

> Сделай голос чуть мягче, но не убирай полностью атмосферу улицы.

GLM преобразует естественный язык в безопасный `ProcessingProfilePatch`, например:

```json
{
  "warmth": "+0.15",
  "presence": "-0.05",
  "ambience_retention": "+0.20",
  "noise_reduction": "-0.10"
}
```

LLM не запускает shell-команды и не формирует произвольные FFmpeg arguments. Изменения проходят schema validation и hard limits.

---

## 9. Telegram Integration

### 9.1 Production requirement

Использовать Local Telegram Bot API Server.

Причина: стандартный Bot API ограничивает скачивание файлов ботом 20 MB, что недостаточно для видео. Local Bot API Server позволяет скачивать файлы без лимита и загружать файлы до 2 GB.

### 9.2 Bot framework

Предлагается `aiogram 3`.

Обязательное разделение:

- Telegram handlers;
- application/use cases;
- domain;
- infrastructure;
- audio worker.

Telegram objects не должны протекать в domain layer.

---

## 10. Backend Architecture

Минимальные сервисы:

### `bot`

- Telegram updates;
- FSM/UI;
- создание jobs;
- отображение статусов;
- отправка результата.

### `api`

FastAPI service для внутренних endpoints, healthchecks и будущего Mini App/admin panel.

### `worker`

- скачивание/получение input;
- FFmpeg;
- вызов provider;
- DSP;
- remux;
- upload result;
- сбор cost/metrics.

### `postgres`

Persistent domain data.

### `redis`

Queue, locks, ephemeral progress, rate limits.

### `object storage`

Production: S3-compatible.

Local development: MinIO или filesystem adapter.

---

## 11. Domain Entities

### User

```text
id
telegram_user_id
created_at
locale
status
referrer_user_id?
free_credits
paid_credits
```

### MediaAsset

```text
id
user_id
kind
original_filename
mime_type
size_bytes
duration_ms
storage_key
sha256
created_at
expires_at
```

### ProcessingJob

```text
id
user_id
source_asset_id
status
preset
processing_profile_json
provider
started_at
finished_at
failure_code
failure_message_internal
output_asset_id
```

### JobCost

```text
job_id
provider_cost_usd
compute_seconds
storage_bytes
telegram_bytes_in
telegram_bytes_out
estimated_total_cost_usd
```

### Payment / CreditLedger

Заложить таблицы в schema до включения платежей.

---

## 12. Job State Machine

```text
CREATED
  ↓
VALIDATING
  ↓
QUEUED
  ↓
PROCESSING
  ↓
REMUXING
  ↓
UPLOADING
  ↓
COMPLETED
```

Ошибка из любой processing-state:

```text
FAILED_RETRYABLE
FAILED_FINAL
```

Отмена:

```text
CANCELLED
```

Jobs должны быть idempotent.

Retry не должен повторно списывать деньги/credits без необходимости.

---

## 13. Media & Quality Requirements

### Video

- отсутствие рассинхронизации audio/video;
- не изменять aspect ratio;
- не менять FPS без необходимости;
- избегать повторного video encoding;
- metadata rotation учитывать корректно.

### Audio

- internal working sample rate: 48 kHz;
- не должно быть clipping;
- true peak контролируется limiter;
- паузы не должны превращаться в цифровую абсолютную тишину, если это делает запись неестественной;
- обработка не должна изменять личность говорящего;
- недопустимы роботизированные металлические артефакты на обычной речи.

---

## 14. Monetization

MVP должен быть технически готов к Telegram Stars, но платежи не должны блокировать первый alpha.

Предлагаемая модель после validation:

### Free

- бесплатный preview;
- первый короткий ролик бесплатно или limited launch campaign.

### Pay-per-use

Цена зависит от продолжительности обработки.

Пример unit:

```text
1 credit = N секунд обработки
```

Это удобнее фиксированной цены «за файл», так как себестоимость связана с длительностью.

### Packs

- 10 минут;
- 30 минут;
- 120 минут.

### Creator subscription

Ежемесячный лимит минут + приоритетная очередь.

Конкретные цены устанавливаются после измерения `cost_per_processed_minute`.

---

## 15. Analytics

События MVP:

```text
start
media_received
media_rejected
preset_selected
preview_started
preview_completed
full_processing_started
full_processing_completed
full_processing_failed
result_downloaded
reprocess_requested
preset_changed
payment_started
payment_completed
```

Основные метрики:

- activation: user → first processed result;
- processing success rate;
- median / p95 processing-time-to-media-duration ratio;
- cost per processed minute;
- preview → full conversion;
- free → paid conversion;
- D1 / D7 / D30 retention;
- repeat processing rate;
- average processed minutes per active user;
- refund/failure rate.

---

## 16. Privacy & Storage

Минимальные требования:

- хранить Telegram token/API keys только в secrets/environment;
- не логировать media binary и private download URLs;
- файлы должны иметь TTL;
- default policy: оригиналы удалить после завершения retention window;
- пользователь должен иметь возможность удалить свою историю/данные;
- database backup не должен содержать сами video/audio binaries;
- storage keys должны быть non-guessable;
- signed URLs — short-lived.

Предлагаемый TTL MVP:

- source: 24 часа после успешной обработки;
- result: 72 часа;
- metadata/cost analytics: дольше, без содержимого медиа.

Политику необходимо сверить с юрисдикцией перед коммерческим запуском.

---

## 17. Reliability

Требования:

- idempotency каждого job;
- retry внешних provider calls с exponential backoff;
- provider timeout;
- circuit breaker / temporary disable provider;
- cleanup зависших jobs;
- storage cleanup worker;
- graceful restart worker без потери очереди;
- duplicate Telegram update не должен создавать duplicate processing charge.

---

## 18. Observability

Логи должны содержать:

```text
request_id
job_id
user_internal_id
stage
duration_ms
provider
retry_count
error_code
```

Не писать в structured logs:

- Telegram message text без необходимости;
- file binary;
- API keys;
- signed URLs;
- provider raw response с чувствительными данными.

Метрики:

- queue depth;
- running jobs;
- job duration;
- provider latency;
- provider errors;
- FFmpeg errors;
- job cost;
- result size;
- processing duration / media duration.

---

## 19. Testing Strategy

### Unit

- profile mapping;
- state machine;
- billing/credit calculations;
- Telegram callback parsing;
- media validation;
- provider error mapping.

### Integration

- ffprobe metadata extraction;
- FFmpeg audio extraction;
- DSP chain;
- remux;
- PostgreSQL repositories;
- Redis queue;
- provider sandbox/mocked HTTP.

### Golden media tests

Хранить небольшой licensed/self-created test corpus.

Проверять автоматически:

- duration drift;
- audio stream exists;
- sample rate;
- peak level;
- output can be decoded;
- no A/V desync beyond threshold;
- video dimensions/fps preserved.

### E2E

Bot update → job → worker → result sending.

В CI внешнюю платную AI-обработку не вызывать на каждый commit. Отдельный scheduled/manual smoke test.

---

## 20. Acceptance Criteria MVP

MVP считается готовым к закрытому alpha, если:

1. Пользователь отправляет MP4 и получает MP4 с улучшенным голосом.
2. Поддерживаются минимум 4 preset.
3. Поддерживается audio/voice input.
4. Видео остаётся визуально идентичным исходнику.
5. A/V sync не нарушается.
6. Минимум 95% корректных тестовых медиа проходят pipeline без ручного вмешательства.
7. Повторный Telegram update не приводит к duplicate job.
8. Ошибка provider отображается пользователю безопасным сообщением.
9. Себестоимость job записывается.
10. Source/result автоматически удаляются по TTL.
11. Есть benchmark report с выбранным speech-enhancement pipeline.
12. На blind listening выбранный pipeline чаще предпочитается original на тестовом наборе.

---

## 21. Out of Scope MVP

Не делать на первой версии:

- генерацию или клонирование голоса;
- изменение личности/тембра на чужой голос;
- AI dubbing;
- транскрибацию;
- автосубтитры;
- монтаж Reels по референсу;
- генерацию B-roll;
- публикацию в соцсети;
- сложную web-панель;
- полноценный Telegram Mini App.

Это сознательно оставляется для следующих продуктов/версий.

---

## 22. Future Features

После подтверждения спроса:

1. GLM-5.3 Flash: редактирование звука естественным языком.
2. Auto subtitles.
3. Silence / filler removal.
4. AI Reels editor.
5. Reference-based editing.
6. Multi-speaker processing.
7. Automatic music ducking.
8. Batch processing.
9. Telegram Mini App.
10. Referral program.
11. Creator subscription.
12. API для B2B / других ботов.

---

## 23. Definition of Product Success

Первое доказательство product-market fit — не количество регистраций, а повторное использование.

После alpha необходимо добиться одновременно:

- пользователи слышат заметную разницу до/после;
- минимум часть пользователей отправляет второй файл без дополнительного объяснения;
- unit economics позволяют устанавливать цену с устойчивой маржой;
- обработка достаточно быстрая, чтобы Telegram UX ощущался удобнее внешнего сайта.
