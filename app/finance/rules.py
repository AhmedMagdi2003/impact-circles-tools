"""Business rules and policy-based expense claim calculations.

All financial calculations use Decimal arithmetic and rounded to cents.
AI model output is treated strictly as an unverified suggestion.
Allowances and exchange conversions are calculated purely server-side.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.integrations.fx import convert_to_eur, get_eur_rate


def money(value: str | Decimal | float | int) -> Decimal:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Enter a valid amount") from exc
    if not result.is_finite() or result < 0 or result > Decimal("10000000"):
        raise ValueError("Amount must be between 0 and 10,000,000")
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_days(start_text: str | None, end_text: str | None) -> int:
    if not start_text or not end_text:
        raise ValueError("Enter the first and last travel dates")
    try:
        start = date.fromisoformat(str(start_text).strip())
        end = date.fromisoformat(str(end_text).strip())
    except ValueError as exc:
        raise ValueError("Enter valid travel dates") from exc
    if end < start:
        raise ValueError("End date must be on or after start date")
    days = (end - start).days + 1
    if not 1 <= days <= 365:
        raise ValueError("Travel must last between 1 and 365 days")
    return days


def calculate_claim(
    details: dict, rate: str, rate_currency: str, include_booking: bool,
    claimed_dates: set[str] | None = None, fx_info: dict | None = None,
) -> dict:
    """Calculate a EUR claim with one allowance per person per travel day."""
    if not str(details.get("amount") or "").strip():
        raise ValueError("Enter the document amount")
    amount = money(details["amount"])
    currency = (details.get("currency") or "").upper().strip()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Enter a three-letter currency code")
    travel = details.get("document_type") in {"flight", "hotel", "train"}
    if travel:
        days = calculate_days(details.get("start_date"), details.get("end_date"))
        start = date.fromisoformat(str(details["start_date"]).strip())
        previously_claimed = claimed_dates or set()
        already_claimed_days = sum(
            (start + timedelta(days=offset)).isoformat() in previously_claimed
            for offset in range(days)
        )
        eligible_days = days - already_claimed_days
        daily = money(rate)
    else:
        days, already_claimed_days, eligible_days = 0, 0, 0
        daily, include_booking = money("0"), True

    if rate_currency.upper() != "EUR":
        raise ValueError("Daily allowance must be set in EUR")
    allowance = (daily * eligible_days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if currency != "EUR" and include_booking:
        if not fx_info or fx_info.get("source_currency") != currency:
            raise ValueError("A current exchange rate is needed to calculate this claim")
        fx_rate = Decimal(str(fx_info["rate"]))
        if not fx_rate.is_finite() or fx_rate <= 0:
            raise ValueError("The exchange rate is invalid")
    else:
        fx_rate = Decimal("1")
    booking_claim = (amount * fx_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if include_booking else Decimal("0.00")
    total = booking_claim + allowance

    return {
        "booking_amount": str(amount),
        "booking_currency": currency,
        "booking_fx_rate": str(fx_rate) if currency != "EUR" and include_booking else "1",
        "booking_fx_date": fx_info.get("date") if fx_info and currency != "EUR" and include_booking else None,
        "booking_fx_provider": fx_info.get("provider") if fx_info and currency != "EUR" and include_booking else None,
        "booking_included": include_booking,
        "booking_claim": str(booking_claim),
        "travel_days": days,
        "already_claimed_days": already_claimed_days,
        "eligible_days": eligible_days,
        "daily_rate": str(daily),
        "daily_allowance": str(allowance),
        "total": str(total),
        "currency": "EUR",
        "total_eur": str(total),
        "rule": ("Calendar days, including departure and return dates; previously claimed days receive no second allowance" if travel
                 else "No daily allowance for other documents"),
    }


def calculate_policy_claim(
    details: dict,
    destination_rate: dict,
    include_booking: bool = True,
    additional_lines: list[dict] | None = None,
    frozen_fx_rates: dict[str, Decimal] | None = None,
) -> dict:
    """Phase 3 Policy-based expense claim calculation with EUR conversion.

    - Models trip separately from documents.
    - Destination-based per diem and hotel ceilings.
    - Multi-line itemization (travel booking, accommodation, per diem, local transport, etc.).
    - Decimal server-side conversion to EUR rounded to cents.
    - Historical FX rate freezing.
    """
    if not str(details.get("amount") or "").strip():
        raise ValueError("Enter the document amount")
    booking_amount = money(details["amount"])
    currency = (details.get("currency") or "").upper().strip()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("Enter a valid three-letter currency code")

    doc_type = (details.get("document_type") or "other").lower()
    is_travel = doc_type in {"flight", "hotel", "train"}
    lines = []

    # 1. Booking receipt line
    explicit_rate = frozen_fx_rates.get(currency) if frozen_fx_rates else None
    booking_eur, booking_rate_info = convert_to_eur(booking_amount, currency, explicit_rate)
    booking_claim_eur = booking_eur if include_booking else Decimal("0.00")

    lines.append({
        "line_type": doc_type if is_travel else "receipt",
        "description": f"{details.get('vendor', 'Booking') or 'Booking'} ({doc_type.title()})",
        "original_amount": str(booking_amount),
        "original_currency": currency,
        "fx_rate": str(booking_rate_info["rate"]),
        "fx_display": booking_rate_info["rate_display"],
        "fx_date": booking_rate_info["date"],
        "fx_provider": booking_rate_info["provider"],
        "eur_amount": str(booking_claim_eur),
        "included_in_total": include_booking,
    })

    # Hotel ceiling verification
    hotel_warning = ""
    if doc_type == "hotel":
        nights = max(1, calculate_days(details.get("start_date"), details.get("end_date")) - 1)
        ceiling_per_night = money(destination_rate.get("hotel_ceiling", "150.00"))
        max_allowed_eur = (ceiling_per_night * nights).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if booking_claim_eur > max_allowed_eur:
            excess = booking_claim_eur - max_allowed_eur
            hotel_warning = f"Accommodation exceeds destination ceiling of {max_allowed_eur} EUR by {excess} EUR for {nights} night(s)."

    # 2. Daily allowance (Per diem)
    if is_travel:
        days = calculate_days(details.get("start_date"), details.get("end_date"))
        daily_rate_eur = money(destination_rate.get("daily_allowance", "35.00"))
        allowance_eur = (daily_rate_eur * days).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        lines.append({
            "line_type": "daily_allowance",
            "description": f"Daily Allowance ({days} days @ {daily_rate_eur} EUR/day - {destination_rate.get('destination', 'Standard')})",
            "original_amount": str(allowance_eur),
            "original_currency": "EUR",
            "fx_rate": "1.0",
            "fx_display": "1.0000",
            "fx_date": booking_rate_info["date"],
            "fx_provider": "Direct (Per Diem Table)",
            "eur_amount": str(allowance_eur),
            "included_in_total": True,
        })
    else:
        days = 0
        daily_rate_eur = money("0.00")
        allowance_eur = Decimal("0.00")

    # 3. Additional itemized lines (local transport, mobile, etc.)
    if additional_lines:
        for extra in additional_lines:
            extra_amt = money(extra.get("amount", "0.00"))
            extra_curr = str(extra.get("currency", "EUR")).upper().strip()
            extra_rate = frozen_fx_rates.get(extra_curr) if frozen_fx_rates else None
            extra_eur, extra_rate_info = convert_to_eur(extra_amt, extra_curr, extra_rate)
            lines.append({
                "line_type": extra.get("line_type", "other"),
                "description": extra.get("description", "Incidental expense"),
                "original_amount": str(extra_amt),
                "original_currency": extra_curr,
                "fx_rate": str(extra_rate_info["rate"]),
                "fx_display": extra_rate_info["rate_display"],
                "fx_date": extra_rate_info["date"],
                "fx_provider": extra_rate_info["provider"],
                "eur_amount": str(extra_eur),
                "included_in_total": True,
            })

    # Sum EUR lines
    total_eur = sum(Decimal(line["eur_amount"]) for line in lines if line.get("included_in_total", True))
    total_eur = total_eur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return {
        "booking_amount": str(booking_amount),
        "booking_currency": currency,
        "booking_included": include_booking,
        "booking_eur": str(booking_claim_eur),
        "booking_fx_rate": str(booking_rate_info["rate"]),
        "booking_fx_provider": booking_rate_info["provider"],
        "booking_fx_date": booking_rate_info["date"],
        "travel_days": days,
        "daily_rate": str(daily_rate_eur),
        "daily_allowance": str(allowance_eur),
        "total": str(total_eur),  # legacy compatibility key
        "total_eur": str(total_eur),
        "currency": "EUR",
        "original_currency": currency,
        "destination": destination_rate.get("destination", "Standard"),
        "hotel_warning": hotel_warning,
        "expense_lines": lines,
        "rule": (
            f"Configured policy: {days} calendar days per diem ({daily_rate_eur} EUR/day) + converted receipts in EUR."
            if is_travel else "Direct receipt converted to EUR at daily reference rate."
        ),
    }
