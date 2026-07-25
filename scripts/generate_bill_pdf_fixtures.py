"""Generate deterministic utility-bill PDF fixtures used by parser and OCR tests."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

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

SCE_SANITIZED_PAGES = [
    [
        "Southern California Edison",
        "sce.com",
        "Your electricity bill",
        "Bill prepared on Jul 22, 2026",
        "Billing period: Jun 22, 2026 through Jul 21, 2026",
        "Total electricity usage: 951 kWh",
        "Daily average usage: 31.7 kWh",
        "Next billing cycle ends Aug 19, 2026",
        "Account number ending 1234",
        "Service account ending 5678",
        "Meter number ending 9012",
    ],
    [
        "Understanding your bill",
        "Payment received $354.15",
        "Previous balance $712.44",
        "Customer service phone 1-800-655-4555",
        "Generic definitions: Baseline Credit and Wildfire Fund Charge",
        "Your Delivery charges include $5.62 in informational components",
    ],
    [
        "Details of your new charges",
        "Your rate: DOMESTIC",
        "Billing period: Jun 22, 2026 through Jul 21, 2026",
        "Delivery charges",
        "Base services charge 30 days x $0.76900 $23.07",
        "Energy-Summer",
        "Tier 1 (within baseline) 579 kWh x $0.17862 $103.42",
        "Tier 2 (over baseline) 372 kWh x $0.27961 $104.01",
        "Wildfire fund charge 951 kWh x $0.00591 $5.62",
        "Generation charges",
        "Energy-Summer",
        "SCE",
        "Tier 1 (within baseline) 579 kWh x $0.11761 $68.10",
        "Tier 2 (over baseline) 372 kWh x $0.11761 $43.75",
        "Other charges or credits",
        "Fixed recovery charge 951 kWh x $0.00619 $5.89",
        "Subtotal of your new charges $353.86",
        "State tax 951 kWh x $0.00030 $0.29",
        "Your new charges $354.15",
        "Additional information",
        "Your summer baseline allowance is 579.0 kWh",
    ],
    ["This page intentionally left blank"],
    [
        "Regulatory notice",
        "CPUC public hearing Sep 30, 2026",
        "Proposed rate increase 13.2%",
        "Application number A.26-01-999",
        "Customer example increase $41.70",
        "Public comment phone 1-866-849-8390",
    ],
    [
        "Usage by Tier",
        "Tier 1 0-579 kWh 579 kWh $0.30/kWh",
        "Tier 2 580+ kWh 372 kWh $0.40/kWh",
        "Rounded average prices; actual prices may vary.",
    ],
]

SCE_SANITIZED_SINGLE_DETAIL_REGIONS = [
    (250, 756, "Go paperless at www.sce.com/ebilling"),
    (520, 716, "Page 3 of 6"),
    (36, 662, "Details of your new charges"),
    (36, 650, "Your rate: DOMESTIC"),
    (36, 640, "Billing period: 06/22/26 to 07/21/26 (30 days)"),
    (36, 620, "Delivery charges"),
    (36, 609, "Base services charge 30 days x $0.76900 $23.07"),
    (36, 599, "Energy-Summer"),
    (45, 588, "Tier 1 (within baseline) 579 kWh x $0.17862 $103.42"),
    (45, 577, "Tier 2 (over baseline) 372 kWh x $0.27961 $104.01"),
    (36, 567, "Wildfire fund charge 951 kWh x $0.00591 $5.62"),
    (36, 547, "Generation charges"),
    (36, 536, "SCE"),
    (36, 525, "Energy-Summer"),
    (45, 515, "Tier 1 (within baseline) 579 kWh x $0.11761 $68.10"),
    (45, 504, "Tier 2 (over baseline) 372 kWh x $0.11761 $43.75"),
    (36, 484, "Other charges or credits"),
    (45, 473, "Fixed recovery charge 951 kWh x $0.00619 $5.89"),
    (36, 458, "Subtotal of your new charges $353.86"),
    (36, 447, "State tax 951 kWh x $0.00030 $0.29"),
    (36, 437, "Your new charges $354.15"),
    (419, 415, "Your Delivery charges include:"),
    (425, 404, "$23.61 transmission charges"),
    (419, 393, "Your Generation charges include:"),
    (425, 382, "-$5.74 PCIA adjustment"),
    (419, 371, "Additional information:"),
    (425, 360, "Service voltage: 240 volts"),
    (425, 349, "Your summer baseline allowance:"),
    (425, 338, "579.0 kWh"),
    (40, 316, "Your Total Usage:"),
    (40, 305, "951 kWh"),
    (228, 305, "Tier 1 Tier 2"),
    (222, 288, "579 kWh 372 kWh"),
    (218, 271, "$0.30/kWh $0.40/kWh"),
    (40, 249, "Understanding Your Bill..."),
    (40, 238, "Average chart prices are rounded and actual prices may vary."),
    (36, 210, "Things you should know"),
    (36, 196, "Fixed Recovery Charge"),
    (
        36,
        182,
        "Generic definitions and regulatory explanations are informational only.",
    ),
]


def draw_text_page(canvas: Any, lines: list[str]) -> None:
    canvas.setFont("Helvetica", 11)
    y = 750
    for line in lines:
        canvas.drawString(54, y, line)
        y -= 28


def text_bill() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen.canvas import Canvas

    output = io.BytesIO()
    canvas = Canvas(output, pagesize=letter, invariant=1, pageCompression=0)
    draw_text_page(canvas, PAGE_ONE)
    canvas.showPage()
    draw_text_page(canvas, PAGE_TWO)
    canvas.save()
    return output.getvalue()


def sanitized_sce_bill() -> bytes:
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    output = io.BytesIO()
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for lines in SCE_SANITIZED_PAGES:
        page = writer.add_blank_page(width=612, height=792)
        commands = ["BT", "/F1 11 Tf", "54 750 Td"]
        for index, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if index:
                commands.append("0 -28 Td")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    writer.add_metadata(
        {
            "/Title": "Sanitized deterministic SCE residential bill fixture",
            "/Producer": "Power Monitor test fixtures",
        }
    )
    writer.write(output)
    return output.getvalue()


def sanitized_sce_single_detail_page() -> bytes:
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    output = io.BytesIO()
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    page = writer.add_blank_page(width=612, height=792)
    commands: list[str] = []
    for x, y, line in SCE_SANITIZED_SINGLE_DETAIL_REGIONS:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.extend(
            [
                "BT",
                "/F1 11 Tf",
                f"{x} {y} Td",
                f"({escaped}) Tj",
                "ET",
            ]
        )
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    writer.add_metadata(
        {
            "/Title": "Sanitized deterministic SCE single detail-page fixture",
            "/Producer": "Power Monitor test fixtures",
        }
    )
    writer.write(output)
    return output.getvalue()


def scanned_bill() -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

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
    if "--sce-only" in sys.argv:
        (FIXTURES / "sanitized-sce-domestic-bill.pdf").write_bytes(sanitized_sce_bill())
        (FIXTURES / "sanitized-sce-single-detail-page.pdf").write_bytes(
            sanitized_sce_single_detail_page()
        )
        return
    text = text_bill()
    (FIXTURES / "text-tiered-bill.pdf").write_bytes(text)
    (FIXTURES / "scanned-tiered-bill.pdf").write_bytes(scanned_bill())
    (FIXTURES / "rotated-tiered-bill.pdf").write_bytes(rotated_bill(text))
    (FIXTURES / "encrypted-tiered-bill.pdf").write_bytes(encrypted_bill(text))
    (FIXTURES / "sanitized-sce-domestic-bill.pdf").write_bytes(sanitized_sce_bill())
    (FIXTURES / "sanitized-sce-single-detail-page.pdf").write_bytes(
        sanitized_sce_single_detail_page()
    )


if __name__ == "__main__":
    main()
