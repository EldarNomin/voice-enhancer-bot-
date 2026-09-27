from voice_enhancer.bot import _start_keyboard, result_keyboard
from voice_enhancer.i18n import MESSAGES, normalize_locale, tr


def test_telegram_language_fallback() -> None:
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("de") == "en"
    assert normalize_locale(None) == "en"


def test_locales_have_matching_messages() -> None:
    assert MESSAGES["ru"].keys() == MESSAGES["en"].keys()
    assert tr("ru", "queued") != tr("en", "queued")


def test_start_buttons_use_telegram_language() -> None:
    russian = _start_keyboard("ru")
    english = _start_keyboard("en")
    assert russian.inline_keyboard[0][0].text == "Как это работает"
    assert english.inline_keyboard[0][0].text == "How it works"


def test_result_keyboard_keeps_source_job_id() -> None:
    keyboard = result_keyboard("a" * 32, "ru")
    assert keyboard.inline_keyboard[0][0].callback_data == "r:" + "a" * 32
    assert keyboard.inline_keyboard[1][0].callback_data == "new:file"
