"""Layer — Localization: locale-aware prompt variants and response formatting
(currency, numbers, dates).

Deliberately dependency-free and not built on Python's stdlib `locale` module:
that module mutates process-global state via locale.setlocale(), which breaks
the instant two requests in different locales are in flight on the same
process — exactly the shared-graph, many-sessions deployment shape this
framework already assumes (see runtime.py's per-thread_id RunBudget). Every
function here is a pure function of (locale_code, value), not process state,
so it's safe under FastAPI's thread-pool execution the same way the
SQLite-thread-safety fix in data_connectors.py had to be.

A locale to use is a per-user preference, not new framework state: store it on
MemoryStore.profiles (context.py already has a free-form profile dict per
user_id) — e.g. memory.update_profile(user_id, locale="en-IN") — and read it
back with memory.get_profile(user_id).get("locale").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class LocaleSpec:
    code: str  # BCP-47, e.g. "en-US", "en-IN"
    currency_symbol: str
    decimal_sep: str
    thousands_sep: str
    date_format: str  # strftime pattern
    grouping: str = "western"  # "western" (3,3,3,...) or "indian" (3,2,2,...)


DEFAULT_LOCALE = "en-US"

_LOCALES: dict[str, LocaleSpec] = {
    "en-US": LocaleSpec("en-US", "$", ".", ",", "%m/%d/%Y"),
    "en-GB": LocaleSpec("en-GB", "£", ".", ",", "%d/%m/%Y"),
    "en-IN": LocaleSpec("en-IN", "₹", ".", ",", "%d/%m/%Y", grouping="indian"),
    "hi-IN": LocaleSpec("hi-IN", "₹", ".", ",", "%d/%m/%Y", grouping="indian"),
}


def register_locale(spec: LocaleSpec) -> None:
    """Add or override a locale — the registry above covers this framework's
    verified use cases (US/UK/India); extend it for any other market."""
    _LOCALES[spec.code] = spec


def resolve_locale(code: str | None) -> LocaleSpec:
    return _LOCALES.get(code or DEFAULT_LOCALE, _LOCALES[DEFAULT_LOCALE])


def _group_western(digits: str) -> str:
    parts = []
    while len(digits) > 3:
        parts.append(digits[-3:])
        digits = digits[:-3]
    parts.append(digits)
    return ",".join(reversed(parts))


def _group_indian(digits: str) -> str:
    """Indian numbering: last 3 digits, then groups of 2 — 1234567 -> 12,34,567."""
    if len(digits) <= 3:
        return digits
    last3, rest = digits[-3:], digits[:-3]
    parts = []
    while len(rest) > 2:
        parts.append(rest[-2:])
        rest = rest[:-2]
    parts.append(rest)
    return ",".join(reversed(parts)) + "," + last3


def format_number(value: float, *, locale: str = DEFAULT_LOCALE, decimals: int = 2) -> str:
    spec = resolve_locale(locale)
    sign = "-" if value < 0 else ""
    whole, _, frac = f"{abs(value):.{decimals}f}".partition(".")
    grouped = (_group_indian if spec.grouping == "indian" else _group_western)(whole)
    grouped = grouped.replace(",", spec.thousands_sep)
    return f"{sign}{grouped}{spec.decimal_sep}{frac}" if decimals else f"{sign}{grouped}"


def format_currency(amount: float, *, locale: str = DEFAULT_LOCALE) -> str:
    spec = resolve_locale(locale)
    return f"{spec.currency_symbol}{format_number(amount, locale=locale)}"


def format_date(d: date | datetime, *, locale: str = DEFAULT_LOCALE) -> str:
    return d.strftime(resolve_locale(locale).date_format)
