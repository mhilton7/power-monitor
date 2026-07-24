"""Generate deterministic utility-bill PDF fixtures used by parser and OCR tests."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "bills"

PAGE_ONE = [
    "Southern California Edison",
    "Customer Name: Example Customer",
    "Service Address: 100 Example Street, Upland, CA",
    "Account number: 0000 1111 2222 1234",
    "Rate Plan: DOMESTIC",
    "Billing Period: Jul 22, 2026 - Aug 20, 2026",
    "Meter ID: MTR-001",
    "Meter ID: MTR-002",
    "Total Usage: 951 kWh",
    "Energy Charges: $322.500000000",
    "Delivery Charges: $170.00",
    "Generation Charges: $152.500000000",
    "Fixed Charges: $20.00",
    "Taxes and Fees: $12.50",
    "Bill Total: $355.00",
    "Current Tier: Tier 2",
    "Projected to stay in Tier 2",
    "28 days remaining",
]

PAGE_TWO = [
    "Usage by Tier",
    "Tier 1 | 0-579 kWh | 579 kWh | $0.30/kWh | $173.7000000",
    "Tier 2 | 580+ kWh | 372 kWh | $0.40/kWh | $148.8000000",
    "Threshold source: account baseline shown on bill; exact interpretation not stated",
    "Payment method: bank account ending 9876",
]


def draw_text_page(canvas: Canvas, lines: list[str]) -> None:
    canvas.setFont("Helvetica", 11)
    y = 750
    for line in lines:
        canvas.drawString(54, y, line)
        y -= 28


def text_bill() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=letter, invariant=1, pageCompression=0)
    draw_text_page(canvas, PAGE_ONE)
    canvas.showPage()
    draw_text_page(canvas, PAGE_TWO)
    canvas.save()
    return output.getvalue()


def scanned_bill() -> bytes:
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=26)
    y = 90
    for line in [*PAGE_ONE, *PAGE_TWO]:
        draw.text((90, y), line, fill="black", font=font)
        y += 66
    output = io.BytesIO()
    canvas = Canvas(output, pagesize=letter, invariant=1, pageCompression=0)
    canvas.drawImage(
        ImageReader(image),
        0,
        0,
        width=letter[0],
        height=letter[1],
        preserveAspectRatio=True,
    )
    canvas.save()
    return output.getvalue()


def rotated_bill(source: bytes) -> bytes:
    reader = PdfReader(io.BytesIO(source))
    writer = PdfWriter()
    writer.add_page(reader.pages[0].rotate(90))
    writer.add_metadata(
        {
            "/Title": "Deterministic rotated utility bill",
            "/Producer": "Power Monitor test fixtures",
        }
    )
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def encrypted_bill(source: bytes) -> bytes:
    reader = PdfReader(io.BytesIO(source))
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt("fixture-password")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    text = text_bill()
    (FIXTURES / "text-tiered-bill.pdf").write_bytes(text)
    (FIXTURES / "scanned-tiered-bill.pdf").write_bytes(scanned_bill())
    (FIXTURES / "rotated-tiered-bill.pdf").write_bytes(rotated_bill(text))
    (FIXTURES / "encrypted-tiered-bill.pdf").write_bytes(encrypted_bill(text))


if __name__ == "__main__":
    main()
