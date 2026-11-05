"""Finance routes: upload, review with EUR policy rules, confirmation, and file retrieval."""

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import app.web
from app.core.auth import (
    redirect,
    render_page,
    require_record_access,
    require_user,
    validate_attachment,
    verify_form_csrf,
)
from app.core.db import database, get_destination_rates, setting, utc_now
from app.finance.prompts import FINANCE_PROMPT
from app.finance.duplicates import find_duplicate
from app.finance.rules import calculate_claim, calculate_policy_claim, money
from app.integrations.fx import get_eur_rate

router = APIRouter()

FINANCE_FIELDS = (
    "document_type", "traveller", "start_date", "end_date", "origin",
    "destination", "vendor", "amount", "currency", "description", "category",
    "invoice_number", "invoice_date",
)
DOCUMENT_TYPES = {"flight", "hotel", "train", "invoice", "receipt", "other"}


def _rates(text: str) -> dict[str, str]:
    rates = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [piece.strip() for piece in line.split(",")]
        if len(parts) != 2:
            raise ValueError("Enter one currency and rate per line, such as EUR,35.50")
        currency, amount = parts
        currency = currency.upper()
        if len(currency) != 3 or not currency.isalpha() or currency in rates:
            raise ValueError("Use each three-letter currency once")
        rates[currency] = str(money(amount))
    if "EUR" not in rates:
        raise ValueError("Set a daily allowance in EUR")
    return {"EUR": rates["EUR"]}


def _claimed_travel_dates(db, owner_id: int, exclude_record_id: int) -> set[str]:
    """Dates already paid a travel allowance on this account's confirmed claims."""
    claimed = set()
    rows = db.execute(
        "SELECT id, trip_start, trip_end, details_json FROM finance_records "
        "WHERE created_by = ? AND confirmed_at IS NOT NULL AND id != ?",
        (owner_id, exclude_record_id),
    )
    for row in rows:
        details = json.loads(row["details_json"])
        if details.get("document_type") not in {"flight", "hotel", "train"}:
            continue
        try:
            start = date.fromisoformat(row["trip_start"] or details.get("start_date", ""))
            end = date.fromisoformat(row["trip_end"] or details.get("end_date", ""))
        except ValueError:
            continue
        span = (end - start).days + 1
        if 1 <= span <= 365:
            claimed.update((start + timedelta(days=offset)).isoformat() for offset in range(span))
    return claimed


def _rule(db, owner_id: int, record_id: int, details: dict) -> dict:
    currency = (details.get("currency") or "").strip().upper()
    travel = details.get("document_type") in {"flight", "hotel", "train"}
    include_booking = setting(db, "claim_include_booking_amount") == "true" if travel else True
    fx = get_eur_rate(currency) if currency != "EUR" and include_booking else None
    return {
        "rates": _rates(setting(db, "per_diem_rates")),
        "include": setting(db, "claim_include_booking_amount"),
        "claimed_dates": sorted(_claimed_travel_dates(db, owner_id, record_id)),
        "fx": ({"source_currency": currency, "rate": str(fx["rate"]), "date": fx["date"],
                "provider": fx["provider"]} if fx else None),
        "details": {key: details.get(key, "") for key in ("document_type", "amount", "currency", "start_date", "end_date")},
    }


def _rule_token(rule: dict) -> str:
    return hashlib.sha256(json.dumps(rule, sort_keys=True).encode()).hexdigest()


def _get_dest_rate(db, destination_name: str) -> dict:
    rates = get_destination_rates(db)
    dest_lower = (destination_name or "").lower().strip()
    for r in rates:
        if r["destination"].lower() in dest_lower or dest_lower in r["destination"].lower():
            return r
    for r in rates:
        if "other" in r["destination"].lower() or "general" in r["destination"].lower():
            return r
    return {
        "destination": destination_name or "General",
        "currency": "EUR",
        "daily_allowance": "35.00",
        "hotel_ceiling": "130.00",
        "effective_date": "2026-01-01",
        "source_notes": "Default standard",
    }


def _claim(db, details: dict, owner_id: int, record_id: int, rule: dict | None = None) -> dict:
    rule = rule or _rule(db, owner_id, record_id, details)
    travel = details.get("document_type") in {"flight", "hotel", "train"}
    include_booking = rule["include"] == "true" if travel else True
    return calculate_claim(
        details, rule["rates"]["EUR"], "EUR", include_booking,
        set(rule["claimed_dates"]), rule["fx"],
    )


def _finance_row(config, record: dict, details: dict, calc: dict) -> list[str]:
    return [
        f"F-{record['id']}",
        record.get("confirmed_at") or "",
        details.get("traveller", ""),
        details.get("document_type", ""),
        details.get("start_date", ""),
        details.get("end_date", ""),
        details.get("origin", ""),
        details.get("destination", ""),
        details.get("vendor", ""),
        details.get("category", ""),
        calc.get("booking_amount", details.get("amount", "")),
        str(calc.get("eligible_days", calc.get("travel_days", 0))),
        calc.get("daily_rate", "0.00"),
        calc.get("daily_allowance", "0.00"),
        calc.get("total_eur", ""),
        "EUR",
        record.get("source_name", ""),
        details.get("description", ""),
        f"{config.public_base_url.rstrip('/')}/finance/{record['id']}" if config.public_base_url else "",
        details.get("currency", ""),
        details.get("invoice_number", ""),
        details.get("invoice_date", ""),
    ]


def _finance_history(db, user: dict) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    base = ("SELECT f.id, f.created_at, f.source_name, f.status, f.sheet_error, f.total_eur, "
            "COALESCE(NULLIF(f.creator_name,''), u.display_name) AS display_name, "
            "COALESCE(NULLIF(f.creator_email,''), u.email) AS email "
            "FROM finance_records f JOIN users u ON u.id=f.created_by ")
    own_rows = [dict(row) for row in db.execute(
        "SELECT id, created_at, source_name, status, details_json, calculation_json FROM finance_records "
        "WHERE created_by = ? ORDER BY id DESC", (user["id"],)
    )]
    usage_by_currency = {
        currency: {"currency": currency, "current_rate": rate, "days": 0, "allowance": Decimal("0.00")}
        for currency, rate in _rates(setting(db, "per_diem_rates")).items()
    }
    for row in own_rows:
        row["details"] = json.loads(row["details_json"])
        calc = json.loads(row["calculation_json"]) if row["calculation_json"] else None
        row["calc"] = calc
        if not calc or not calc.get("travel_days"):
            continue
        summary = usage_by_currency["EUR"]
        summary["days"] += int(calc.get("eligible_days", calc["travel_days"]))
        if calc["currency"] == "EUR":
            summary["allowance"] += Decimal(calc["daily_allowance"])
    usage = [
        {**item, "allowance": f"{item['allowance']:.2f}"}
        for item in sorted(usage_by_currency.values(), key=lambda item: item["currency"])
    ]
    if user["role"] == "admin":
        recent = [dict(row) for row in db.execute(base + "ORDER BY f.id DESC LIMIT 100")]
        pending = [dict(row) for row in db.execute(base + "WHERE f.status='Needs admin review' ORDER BY f.id DESC")]
    else:
        recent, pending = [], []
    return recent, pending, own_rows, usage


@router.get("/finance")
def finance_list(request: Request):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        rows, pending, own_rows, usage = _finance_history(db, user)
    return render_page("finance.html", user=user, rows=rows, pending=pending,
                       own_rows=own_rows, usage=usage, error="")


@router.get("/source/finance/{record_id}")
def source_finance(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        row = require_record_access(db, "finance_records", record_id, user)
    path = config.database_path.parent / "uploads" / f"finance-{record_id}"
    if not row or not row["source_name"] or not path.is_file():
        raise HTTPException(404, "Source file not found")
    return FileResponse(path, media_type=row["source_mime"], headers={"Content-Disposition": "inline"})


@router.post("/finance/upload")
async def finance_upload(request: Request, file: UploadFile = File(...), csrf: str = Form(...)):
    config = request.app.state.config
    user = require_user(request, config)
    if csrf != user["csrf_token"]:
        raise HTTPException(403, "Refresh and try again")
    try:
        attachment = await validate_attachment(file)
        if not attachment:
            raise ValueError("Choose a document")
        digest = hashlib.sha256(attachment.data).hexdigest()
        result = await app.web.ask_model(
            config, config.finance_provider, config.finance_model, FINANCE_PROMPT, attachment=attachment
        )
        details = {key: str(result.get(key) or "").strip()[:500] for key in FINANCE_FIELDS}
        if details["document_type"] not in DOCUMENT_TYPES:
            details["document_type"] = "other"
        with database(config.database_path) as db:
            categories = [c.strip() for c in setting(db, "categories").split(",") if c.strip()]
            preferred = "Travel" if details["document_type"] in {"flight", "hotel", "train"} else "Other"
            details["category"] = preferred if preferred in categories else (categories[0] if categories else "")
            duplicate_id = find_duplicate(db, digest, details)
            db.execute("""
                INSERT INTO finance_records
                (created_at, created_by, source_name, source_mime, source_hash, status, details_json, source_currency, destination, duplicate_of, creator_name, creator_email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                utc_now(), user["id"], attachment.name, attachment.mime, digest,
                "Needs admin review" if duplicate_id else "Needs your attention", json.dumps(details),
                details.get("currency", "EUR"), details.get("destination", ""), duplicate_id,
                user["display_name"] or user["email"].split("@", 1)[0], user["email"]
            ))
            record_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            folder = config.database_path.parent / "uploads"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"finance-{record_id}").write_bytes(attachment.data)
        return redirect(f"/finance/{record_id}")
    except ValueError as exc:
        with database(config.database_path) as db:
            rows, pending, own_rows, usage = _finance_history(db, user)
        return render_page("finance.html", user=user, rows=rows, pending=pending,
                           own_rows=own_rows, usage=usage, error=str(exc))


@router.get("/finance/{record_id}")
def finance_review(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        row = require_record_access(db, "finance_records", record_id, user)
        record = dict(row)
        details = json.loads(record["details_json"])
        categories = [c.strip() for c in setting(db, "categories").split(",") if c.strip()]
        duplicate_id = find_duplicate(db, record["source_hash"], details, record_id)
        duplicate = {"id": duplicate_id} if duplicate_id else None

        dest_rates = get_destination_rates(db)
        dest_name = details.get("destination", "")
        dest_rate = _get_dest_rate(db, dest_name)
        try:
            rule = _rule(db, row["created_by"], record_id, details) if not record["confirmed_at"] else {
                "rates": {"EUR": json.loads(record["calculation_json"])["daily_rate"]},
                "include": "true", "claimed_dates": [], "fx": None,
            }
            calc = (json.loads(record["calculation_json"]) if record["calculation_json"]
                    else _claim(db, details, row["created_by"], record_id, rule))
            calculation_error = ""
        except ValueError as exc:
            calc, calculation_error = None, str(exc)
            rule = {"rates": _rates(setting(db, "per_diem_rates")), "include": setting(db, "claim_include_booking_amount"),
                    "claimed_dates": [], "fx": None}

    return render_page(
        "finance_review.html",
        user=user,
        record=record,
        details=details,
        categories=categories,
        destination_rates=dest_rates,
        selected_destination=dest_rate.get("destination", ""),
        calc=calc,
        calculation_error=calculation_error,
        duplicate=duplicate,
        error="",
        rule=rule,
        rule_token=_rule_token(rule),
    )


@router.get("/finance/{record_id}/confirmed")
def finance_confirmed(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        row = require_record_access(db, "finance_records", record_id, user)
        if not row["confirmed_at"]:
            return redirect(f"/finance/{record_id}")
        record = dict(row)
    return render_page(
        "finance_confirmed.html",
        user=user,
        record=record,
        calc=json.loads(record["calculation_json"]),
    )


@router.post("/finance/{record_id}/delete")
async def finance_delete_draft(request: Request, record_id: int):
    """Let an uploader or admin discard an unconfirmed claim and its file."""
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    if form.get("confirm_delete") != "yes":
        raise HTTPException(400, "Confirm draft deletion")
    with database(config.database_path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = require_record_access(db, "finance_records", record_id, user)
        if row["confirmed_at"] or row["sheet_synced_at"]:
            raise HTTPException(409, "Confirmed claims cannot be deleted. Contact finance for a correction.")
        db.execute("DELETE FROM finance_records WHERE id=?", (record_id,))
    (config.database_path.parent / "uploads" / f"finance-{record_id}").unlink(missing_ok=True)
    return redirect("/finance")


@router.post("/finance/{record_id}/confirm")
async def finance_confirm(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    details = {key: str(form.get(key, "")).strip()[:500] for key in FINANCE_FIELDS}
    if details["document_type"] not in DOCUMENT_TYPES:
        raise HTTPException(400, "Choose a document type")

    with database(config.database_path) as db:
        # Serialize the final duplicate check and confirmation across concurrent requests.
        db.execute("BEGIN IMMEDIATE")
        row = require_record_access(db, "finance_records", record_id, user)
        if row["confirmed_at"]:
            return redirect(f"/finance/{record_id}/confirmed")
        categories = [c.strip() for c in setting(db, "categories").split(",") if c.strip()]
        if details["category"] not in categories:
            raise HTTPException(400, "Choose a listed category")
        duplicate_id = find_duplicate(db, row["source_hash"], details, record_id)
        override = str(form.get("override_duplicate", "")) == "yes"
        reason = str(form.get("duplicate_override_reason", "")).strip()[:500]
        if duplicate_id and (user["role"] != "admin" or not override or not reason):
            db.execute("UPDATE finance_records SET details_json=?, status='Needs admin review', duplicate_of=? WHERE id=?",
                       (json.dumps(details), duplicate_id, record_id))
            return redirect(f"/finance/{record_id}")
        try:
            rule = _rule(db, row["created_by"], record_id, details)
        except ValueError as exc:
            return render_page("finance_review.html", user=user, record=dict(row), details=details,
                               categories=categories, calc=None, calculation_error=str(exc),
                               duplicate={"id": duplicate_id} if duplicate_id else None,
                               error="The daily exchange rate is unavailable. Try again later.",
                               rule={"rates": {}, "include": "true", "claimed_dates": [], "fx": None}, rule_token="")
        if str(form.get("rule_token", "")) != _rule_token(rule):
            try:
                refreshed_calc = _claim(db, details, row["created_by"], record_id, rule)
                refreshed_error = ""
            except ValueError as exc:
                refreshed_calc, refreshed_error = None, str(exc)
            return render_page(
                "finance_review.html",
                user=user,
                record=dict(row),
                details=details,
                categories=categories,
                calc=refreshed_calc,
                calculation_error=refreshed_error,
                duplicate={"id": duplicate_id} if duplicate_id else None,
                error="The allowance settings, exchange rate, or a travel claim changed. Check the new total before confirming.",
                rule=rule,
                rule_token=_rule_token(rule),
            )
        try:
            calc = _claim(db, details, row["created_by"], record_id, rule)
        except ValueError as exc:
            return render_page(
                "finance_review.html",
                user=user,
                record=dict(row),
                details=details,
                categories=categories,
                calc=None,
                calculation_error=str(exc),
                duplicate={"id": duplicate_id} if duplicate_id else None,
                error="Correct the fields and confirm again",
                rule=rule,
                rule_token=_rule_token(rule),
            )

        total_eur = calc.get("total_eur") or calc.get("total", "0.00")
        source_curr = details.get("currency", "EUR")
        recorded_fx_rate = calc["booking_fx_rate"]

        db.execute("""
            UPDATE finance_records
            SET status = 'Confirmed', details_json = ?, calculation_json = ?, confirmed_at = ?,
                total_eur = ?, source_currency = ?, destination = ?, trip_start = ?, trip_end = ?,
                duplicate_of = ?, confirmed_by = ?, duplicate_override_reason = ?, actor_name = ?, actor_email = ?
            WHERE id = ?
        """, (
            json.dumps(details),
            json.dumps(calc),
            utc_now(),
            total_eur,
            source_curr,
            details.get("destination", ""),
            details.get("start_date", ""),
            details.get("end_date", ""),
            duplicate_id,
            user["id"],
            reason if duplicate_id else None,
            user["display_name"] or user["email"].split("@", 1)[0],
            user["email"],
            record_id,
        ))

        # Record itemized expense line
        db.execute("DELETE FROM expense_lines WHERE record_id = ?", (record_id,))
        db.execute("""
            INSERT INTO expense_lines
            (record_id, line_type, description, original_amount, original_currency,
             fx_rate, fx_date, fx_provider, eur_amount, evidence_doc)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_id,
            details.get("document_type", "receipt"),
            f"{details.get('vendor', 'Booking')} - {details.get('description', '')}",
            calc.get("booking_amount", "0.00"),
            source_curr,
            recorded_fx_rate,
            calc.get("booking_fx_date") or utc_now()[:10],
            calc.get("booking_fx_provider") or "EUR receipt",
            calc["booking_claim"],
            row["source_name"],
        ))
        if Decimal(calc["daily_allowance"]) > 0:
            db.execute("""
                INSERT INTO expense_lines
                (record_id, line_type, description, original_amount, original_currency,
                 fx_rate, fx_date, fx_provider, eur_amount, evidence_doc)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id, "daily_allowance",
                f"{calc['eligible_days']} days at {calc['daily_rate']} EUR per day",
                calc["daily_allowance"], "EUR", "1", utc_now()[:10],
                "Fixed admin setting", calc["daily_allowance"], row["source_name"],
            ))

    await app.web.sync_record(config, "finance", record_id)
    return redirect(f"/finance/{record_id}/confirmed")


@router.post("/finance/{record_id}/sync")
async def finance_sync_retry(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    with database(config.database_path) as db:
        require_record_access(db, "finance_records", record_id, user)
    await app.web.sync_record(config, "finance", record_id)
    return redirect(f"/finance/{record_id}/confirmed")
