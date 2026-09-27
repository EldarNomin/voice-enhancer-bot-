"""Small Russian/English catalog for the Telegram interface."""

MESSAGES = {
    "ru": {
        "start": (
            "🎙 Отправь мне видео, аудио или голосовое.\n"
            "Я очищу голос, уберу лишний шум и сделаю звучание плотнее и ближе к профессиональному микрофону."
        ),
        "unsupported_format": "Этот формат файла пока не поддерживается.",
        "file_too_large": "Размер файла превышает допустимый лимит.",
        "file_too_long": "Длительность файла должна быть не более 10 минут.",
        "no_audio": "В файле не найдена аудиодорожка.",
        "no_video": "В файле не найдена видеодорожка.",
        "telegram_file_error": "Не удалось получить файл из Telegram. Попробуй позже.",
        "duplicate_file": "Этот файл уже принят. Выбери стиль в предыдущем сообщении.",
        "read_error": "Не удалось прочитать файл. Проверь формат и попробуй ещё раз.",
        "choose_preset": "Файл получен. Как обработать голос?",
        "unknown_preset": "Неизвестный пресет.",
        "stale_preset": "Файл уже поставлен в очередь или ссылка устарела.",
        "queued_ack": "Поставил в очередь.",
        "queued": "Файл в очереди. Скоро пришлю результат.",
        "processing": "Обрабатываю голос…",
        "remuxing": "Собираю видео…",
        "video_ready": "Готово! Видео с обработанным голосом.",
        "audio_ready": "Готово! Аудио с обработанным голосом.",
        "completed": "Готово! Результат отправлен.",
        "failed": "Не удалось обработать файл. Пришли его ещё раз чуть позже.",
    },
    "en": {
        "start": (
            "🎙 Send me a video, audio file, or voice message.\n"
            "I'll reduce background noise and make your voice sound fuller and clearer."
        ),
        "unsupported_format": "This file format is not supported yet.",
        "file_too_large": "The file exceeds the size limit.",
        "file_too_long": "The file must be no longer than 10 minutes.",
        "no_audio": "No audio track was found in this file.",
        "no_video": "No video track was found in this file.",
        "telegram_file_error": "Could not get the file from Telegram. Please try again later.",
        "duplicate_file": "This file was already received. Choose a style in the previous message.",
        "read_error": "Could not read the file. Check its format and try again.",
        "choose_preset": "File received. How should I enhance the voice?",
        "unknown_preset": "Unknown preset.",
        "stale_preset": "The file is already queued or this selection has expired.",
        "queued_ack": "Added to the queue.",
        "queued": "Your file is queued. I'll send the result soon.",
        "processing": "Enhancing the voice…",
        "remuxing": "Preparing the video…",
        "video_ready": "Done! Here is your video with enhanced voice.",
        "audio_ready": "Done! Here is your audio with enhanced voice.",
        "completed": "Done! The result has been sent.",
        "failed": "Could not process the file. Please try sending it again later.",
    },
}


def normalize_locale(language_code: str | None) -> str:
    return "ru" if language_code and language_code.lower().split("-", 1)[0] == "ru" else "en"


def tr(locale: str, key: str) -> str:
    return MESSAGES[normalize_locale(locale)][key]
