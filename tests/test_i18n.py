from voice_enhancer.i18n import MESSAGES, normalize_locale, tr


def test_telegram_language_fallback() -> None:
    assert normalize_locale("ru-RU") == "ru"
    assert normalize_locale("en-US") == "en"
    assert normalize_locale("de") == "en"
    assert normalize_locale(None) == "en"


def test_locales_have_matching_messages() -> None:
    assert MESSAGES["ru"].keys() == MESSAGES["en"].keys()
    assert tr("ru", "queued") != tr("en", "queued")
