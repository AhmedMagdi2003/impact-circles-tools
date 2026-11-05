"""Create clearly marked synthetic PDFs for manual expense-claim acceptance tests.

Requires reportlab. Run with the bundled PDF runtime or a Python installation
that has reportlab installed. These files are test evidence, not real expenses.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


OUTPUT = Path(__file__).resolve().parents[1] / "output" / "pdf" / "claim-test-documents"
RED = colors.HexColor("#C9222A")
INK = colors.HexColor("#20252A")
MUTED = colors.HexColor("#59636D")

DOCUMENTS = [
    {
        "filename": "01_alice_flight_3_days.pdf", "title": "Round-trip flight ticket",
        "vendor": "Sample Air", "reference": "SA-TEST-101", "traveller": "Alice Example",
        "fields": [("Origin", "Cairo"), ("Destination", "Berlin"),
                   ("Outbound travel date", "2026-11-10"), ("Return travel date", "2026-11-12")],
        "amount": "120.00 EUR",
    },
    {
        "filename": "02_alice_hotel_overlap.pdf", "title": "Hotel booking invoice",
        "vendor": "Sample Hotel", "reference": "SH-TEST-202", "traveller": "Alice Example",
        "fields": [("Origin", "Berlin"), ("Destination / hotel city", "Berlin"),
                   ("Check-in date", "2026-11-11"), ("Check-out date", "2026-11-12")],
        "amount": "90.00 EUR",
    },
    {
        "filename": "03_alice_train_one_new_day.pdf", "title": "Train ticket - two travel dates",
        "vendor": "Sample Rail", "reference": "SR-TEST-303", "traveller": "Alice Example",
        "fields": [("Origin", "Berlin"), ("Destination", "Leipzig"),
                   ("First travel date", "2026-11-12"), ("Last travel date", "2026-11-13")],
        "amount": "40.00 EUR",
    },
    {
        "filename": "04_alice_ordinary_invoice.pdf", "title": "Office supplies invoice",
        "vendor": "Sample Office Supplies", "reference": "INV-TEST-404", "traveller": "",
        "fields": [("Invoice number", "INV-TEST-404"), ("Invoice date", "2026-11-14"),
                   ("Description", "Office stationery - no travel")],
        "amount": "50.00 EUR",
    },
    {
        "filename": "05_alice_high_value_invoice.pdf", "title": "Equipment invoice",
        "vendor": "Sample Equipment Co.", "reference": "INV-TEST-505", "traveller": "",
        "fields": [("Invoice number", "INV-TEST-505"), ("Invoice date", "2026-11-15"),
                   ("Description", "Equipment purchase - no travel")],
        "amount": "500.00 EUR",
    },
    {
        "filename": "06_alice_valid_december_flight.pdf", "title": "December round-trip flight ticket",
        "vendor": "Sample Air C", "reference": "SC-TEST-606", "traveller": "Alice Example",
        "fields": [("Origin", "Cairo"), ("Destination", "Berlin"),
                   ("Outbound travel date", "2026-12-01"), ("Return travel date", "2026-12-03")],
        "amount": "100.00 EUR",
    },
    {
        "filename": "07_bob_same_dates_separate_account.pdf", "title": "Round-trip flight ticket",
        "vendor": "Sample Air B", "reference": "SB-TEST-707", "traveller": "Bob Example",
        "fields": [("Origin", "Cairo"), ("Destination", "Berlin"),
                   ("Outbound travel date", "2026-11-10"), ("Return travel date", "2026-11-12")],
        "amount": "80.00 EUR",
    },
    {
        "filename": "boundary_invalid_366_day_flight.pdf", "title": "Long-span flight ticket - rejection test",
        "vendor": "Sample Long Journey", "reference": "SL-TEST-999", "traveller": "Alice Example",
        "fields": [("Origin", "Cairo"), ("Destination", "Berlin"),
                   ("Outbound travel date", "2026-01-01"), ("Return travel date", "2027-01-01")],
        "amount": "100.00 EUR",
    },
]


def make_pdf(doc: dict) -> Path:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    path = OUTPUT / doc["filename"]
    pdf = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4

    pdf.setFillColor(colors.HexColor("#F6F7F8"))
    pdf.rect(0, 0, width, height, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.rect(42, 67, width - 84, height - 134, fill=1, stroke=0)
    pdf.setFillColor(RED)
    pdf.rect(42, height - 142, width - 84, 6, fill=1, stroke=0)

    pdf.setFont("Helvetica-Bold", 13)
    pdf.drawString(62, height - 85, "SYNTHETIC TEST DOCUMENT")
    pdf.setFont("Helvetica", 9)
    pdf.setFillColor(MUTED)
    pdf.drawString(62, height - 105, "Not a real ticket, invoice, booking, or request for payment")

    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 23)
    pdf.drawString(62, height - 190, doc["title"])
    pdf.setFont("Helvetica", 11)
    pdf.drawString(62, height - 221, doc["vendor"])
    pdf.setFillColor(MUTED)
    pdf.drawString(62, height - 249, f"Reference: {doc['reference']}")

    y = height - 307
    rows = ([('Passenger / guest', doc["traveller"])] if doc["traveller"] else []) + doc["fields"]
    for label, value in rows:
        pdf.setStrokeColor(colors.HexColor("#E1E4E7"))
        pdf.line(62, y - 10, width - 62, y - 10)
        pdf.setFont("Helvetica", 10)
        pdf.setFillColor(MUTED)
        pdf.drawString(62, y + 4, label)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.setFillColor(INK)
        pdf.drawRightString(width - 62, y + 4, value)
        y -= 39

    amount_y = max(y - 25, 185)
    pdf.setFillColor(colors.HexColor("#FFF0F1"))
    pdf.roundRect(62, amount_y - 47, width - 124, 65, 8, fill=1, stroke=0)
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(77, amount_y - 7, "Document amount")
    pdf.setFont("Helvetica-Bold", 18)
    pdf.drawRightString(width - 77, amount_y - 12, doc["amount"])

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(62, 98, "For testing AI extraction and fixed claim rules only.")
    pdf.drawString(62, 83, "No per diem or final claim total is printed on this source document.")
    pdf.setTitle(doc["title"] + " - synthetic test")
    pdf.save()
    return path


if __name__ == "__main__":
    for document in DOCUMENTS:
        print(make_pdf(document))
