"""Foreign exchange rate adapter.

Fetches daily ExchangeRate-API reference rates to convert foreign currencies
to EUR with thread-safe caching and deterministic Decimal rounding.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from threading import Lock
from time import monotonic

import httpx

ER_API_URL = "https://open.er-api.com/v6/latest/EUR"

# Thread-safe in-memory rate cache: (rates_dict, rate_date, provider, fetch_monotonic)
_CACHE: dict[str, tuple[dict[str, Decimal], str, str, float]] = {}
_CACHE_LOCK = Lock()
CACHE_TTL_SECONDS = 12 * 3600
MAX_RATE_AGE_SECONDS = 36 * 3600


def _get_cached_rates() -> tuple[dict[str, Decimal], str, str] | None:
    now = monotonic()
    with _CACHE_LOCK:
        if "EUR_BASE" in _CACHE:
            rates, rate_date, provider, fetched_at = _CACHE["EUR_BASE"]
            if now - fetched_at < CACHE_TTL_SECONDS:
                return rates.copy(), rate_date, provider
    return None


def _set_cached_rates(rates: dict[str, Decimal], rate_date: str, provider: str) -> None:
    with _CACHE_LOCK:
        _CACHE["EUR_BASE"] = (rates.copy(), rate_date, provider, monotonic())


def fetch_eur_rates() -> tuple[dict[str, Decimal], str, str]:
    """Fetch daily EUR-based rates; never substitute guessed or undated rates."""
    cached = _get_cached_rates()
    if cached:
        return cached

    try:
        response = httpx.get(ER_API_URL, timeout=4.0, follow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if data.get("result") != "success" or data.get("base_code") != "EUR":
            raise ValueError("The exchange-rate service returned an invalid EUR response")
        updated = datetime.fromtimestamp(int(data["time_last_update_unix"]), timezone.utc)
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if not 0 <= age <= MAX_RATE_AGE_SECONDS:
            raise ValueError("The exchange-rate service has not supplied a recent daily rate")
        rates = {code: Decimal(str(rate)) for code, rate in data["rates"].items()}
        if any(not rate.is_finite() or rate <= 0 for rate in rates.values()):
            raise ValueError("The exchange-rate service returned an invalid rate")
        rates["EUR"] = Decimal("1")
        parsed_date = updated.date().isoformat()
        provider = "ExchangeRate-API Open Access"
        _set_cached_rates(rates, parsed_date, provider)
        return rates, parsed_date, provider
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, InvalidOperation, AttributeError) as exc:
        raise ValueError("Daily exchange rates are unavailable. Please try again later; this claim has not been confirmed.") from exc


def get_eur_rate(currency: str) -> dict:
    """Return the exchange rate from source currency to EUR.

    Rate represents: 1 unit of foreign currency = X EUR.
    So EUR_amount = foreign_amount * rate.
    """
    currency = currency.strip().upper()
    if not currency or len(currency) != 3 or not currency.isalpha():
        raise ValueError(f"Invalid currency code: {currency}")

    if currency == "EUR":
        today = datetime.now(timezone.utc).date().isoformat()
        return {
            "source_currency": "EUR",
            "target_currency": "EUR",
            "rate": Decimal("1.0"),
            "rate_display": "1.0000",
            "date": today,
            "provider": "Direct (Identity)",
            "provider_url": "https://ecb.europa.eu",
        }

    rates_per_eur, rate_date, provider = fetch_eur_rates()
    if currency not in rates_per_eur:
        raise ValueError(f"No exchange rate found for currency: {currency}")

    # rates_per_eur has: 1 EUR = N units of currency
    # Therefore: 1 unit of currency = (1 / N) EUR
    foreign_units_per_eur = rates_per_eur[currency]
    if foreign_units_per_eur <= 0:
        raise ValueError(f"Invalid rate for currency {currency}: {foreign_units_per_eur}")

    rate_to_eur = (Decimal("1.0") / foreign_units_per_eur)

    return {
        "source_currency": currency,
        "target_currency": "EUR",
        "rate": rate_to_eur,
        "rate_display": f"{rate_to_eur:.6f}",
        "units_per_eur": foreign_units_per_eur,
        "date": rate_date,
        "provider": provider,
        "provider_url": "https://www.exchangerate-api.com",
    }


def convert_to_eur(amount: str | Decimal | float, currency: str, explicit_rate: Decimal | None = None) -> tuple[Decimal, dict]:
    """Convert an amount in currency to EUR, rounded to cents."""
    dec_amount = Decimal(str(amount))
    if dec_amount < 0:
        raise ValueError("Amount cannot be negative")

    currency = currency.strip().upper()
    if explicit_rate is not None:
        rate = explicit_rate
        rate_info = {
            "source_currency": currency,
            "target_currency": "EUR",
            "rate": rate,
            "rate_display": f"{rate:.6f}",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "provider": "Manual / Frozen Rate",
            "provider_url": "",
        }
    else:
        rate_info = get_eur_rate(currency)
        rate = rate_info["rate"]

    eur_cents = (dec_amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return eur_cents, rate_info
