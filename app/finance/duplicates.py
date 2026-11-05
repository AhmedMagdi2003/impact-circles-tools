"""Find confirmed documents that may represent the same expense."""

import json
import unicodedata


def _normalized(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", str(value or "")).casefold() if ch.isalnum())


def find_duplicate(db, source_hash: str, details: dict, exclude_id: int | None = None):
    """Exact bytes, or same invoice number and vendor, require an admin decision."""
    number = _normalized(details.get("invoice_number", ""))
    vendor = _normalized(details.get("vendor", ""))
    rows = db.execute("""SELECT id, source_hash, details_json FROM finance_records
                         WHERE confirmed_at IS NOT NULL AND id != ? ORDER BY id DESC""",
                      (exclude_id or 0,))
    for row in rows:
        if source_hash and row["source_hash"] == source_hash:
            return row["id"]
        if not number or not vendor:
            continue
        try:
            previous = json.loads(row["details_json"])
        except (ValueError, TypeError):
            continue
        if not isinstance(previous, dict):
            continue
        if (number == _normalized(previous.get("invoice_number", "")) and
                vendor == _normalized(previous.get("vendor", ""))):
            return row["id"]
    return None
