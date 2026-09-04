from datetime import date

import pytest

from agent_foundry.i18n import (
    LocaleSpec, format_currency, format_date, format_number, register_locale,
    resolve_locale,
)


def test_format_currency_uses_indian_lakh_crore_grouping():
    assert format_currency(1234567.5, locale="en-IN") == "₹12,34,567.50"


def test_format_currency_uses_western_thousands_grouping():
    assert format_currency(1234567.5, locale="en-US") == "$1,234,567.50"


def test_format_number_handles_negative_values():
    assert format_number(-1234, locale="en-IN", decimals=0) == "-1,234"


def test_format_date_respects_locale_pattern():
    d = date(2026, 9, 3)
    assert format_date(d, locale="en-IN") == "03/09/2026"
    assert format_date(d, locale="en-US") == "09/03/2026"


def test_unknown_locale_falls_back_to_default_not_a_crash():
    assert resolve_locale("xx-ZZ").code == "en-US"


def test_register_locale_adds_a_new_market():
    register_locale(LocaleSpec("fr-FR", "€", ",", " ", "%d/%m/%Y"))
    assert format_currency(1234.5, locale="fr-FR") == "€1 234,50"


def test_grouping_is_a_pure_function_not_process_locale_state():
    """Two 'requests' in different locales interleaved on the same process
    must not bleed into each other — the real bug Python's stdlib locale
    module has (locale.setlocale is process-global)."""
    assert format_currency(1000, locale="en-IN") == "₹1,000.00"
    assert format_currency(1000, locale="en-US") == "$1,000.00"
    assert format_currency(1000, locale="en-IN") == "₹1,000.00"
