"""Builds a synthetic multi-page tender PDF used to smoke-test the pipeline.

Usage: python samples/make_sample_pdf.py [out.pdf]
"""
import sys
import textwrap
from pathlib import Path

PAGES = [
    (
        "INVITATION TO TENDER",
        "Reference: ITMS/2026/0417",
        "",
        "Procurement Authority: Northern Regional Health Board",
        "Contract title: Managed IT Infrastructure Services 2026-2029",
        "",
        "1. The Authority invites sealed tenders for the provision of managed IT",
        "   infrastructure, end-user support and cybersecurity services across four",
        "   hospital sites for a period of 36 months, extendable by 24 months.",
        "",
        "2. Tenders must be submitted in English. Any document not provided in",
        "   English must be accompanied by a certified English translation.",
        "",
        "3. Clarification questions must be submitted in writing no later than",
        "   2026-03-02 at 12:00. The Authority is not obliged to respond to",
        "   questions raised after this date.",
        "",
        "4. The deadline for submission of tenders is 2026-03-20 at 14:00 GMT.",
        "   Tenders received after the closing time will be rejected without",
        "   further notice.",
        "",
        "5. Tenders shall remain valid and irrevocable for 120 days from the",
        "   closing date.",
    ),
    (
        "SECTION B - MANDATORY REQUIREMENTS",
        "",
        "B1. The tenderer must hold ISO 27001:2022 certification for information",
        "    security management and must provide the certificate number.",
        "",
        "B2. The tenderer shall hold public liability insurance of not less than",
        "    EUR 5,000,000 per occurrence and professional indemnity insurance of",
        "    not less than EUR 2,000,000.",
        "",
        "B3. The tenderer must demonstrate an average annual turnover of at least",
        "    EUR 4,500,000 over the last three financial years, evidenced by audited",
        "    financial statements.",
        "",
        "B4. The tenderer must have completed at least five similar contracts of",
        "    not less than EUR 800,000 each within the last five years.",
        "",
        "B5. The proposed project director must hold a PMP or PRINCE2",
        "    certification and must be available for at least 60 percent of the",
        "    contract period.",
        "",
        "B6. Any tenderer currently subject to litigation, or declaring pending",
        "    legal proceedings, must attach a full written explanation on its own",
        "    letterhead. Failure to disclose litigation will render the tender",
        "    non-responsive.",
        "",
        "B7. Subcontracting of any part of the works is not permitted without the",
        "    prior written consent of the Authority. Any proposed subcontractor",
        "    must be named in the submission.",
        "",
        "Failure to meet any requirement marked B1 to B7 will result in the tender",
        "being rejected as non-compliant.",
    ),
    (
        "SECTION C - SUBMISSION REQUIREMENTS",
        "",
        "C1. Every tenderer must submit the following documents. A submission",
        "    missing any of these documents will be treated as incomplete and will",
        "    be rejected:",
        "    (a) Technical proposal, maximum 25 pages, excluding appendices;",
        "    (b) Commercial proposal and completed price schedule;",
        "    (c) Bill of quantities in the format issued with this document;",
        "    (d) Audited financial statements for the last three years;",
        "    (e) ISO 27001:2022 certificate;",
        "    (f) Insurance certificates evidencing the limits stated in B2;",
        "    (g) Curriculum vitae of all key personnel;",
        "    (h) References for the five contracts described in B4;",
        "    (i) Declaration of no conflict of interest, signed by a director.",
        "",
        "C2. A bid bond of EUR 50,000 must be submitted with the tender, in the",
        "    form of a bank guarantee valid until 150 days after the closing date.",
        "    The bid bond will be forfeited if the successful tenderer withdraws",
        "    after award.",
        "",
        "C3. Tenders must be submitted electronically through the portal at",
        "    https://procurement.example.org and also as one sealed hard copy",
        "    delivered to the address in paragraph 6. Submissions exceeding 25 pages",
        "    may be rejected.",
        "",
        "C4. Prices must be submitted in euro, excluding VAT, as firm fixed prices.",
        "    The price validity period is 120 days.",
        "",
        "C5. The Authority reserves the right to cancel or withdraw this tender at",
        "    any time without liability to any tenderer.",
    ),
]

WIDTH = 105


def escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def content_stream(lines: list[str]) -> bytes:
    out = []
    y = 750
    for raw in lines:
        for line in textwrap.wrap(raw, WIDTH) or [""]:
            bold = raw.strip() and raw == raw.strip() and raw.isupper()
            font = "/F2 11 Tf" if bold else "/F1 10 Tf"
            out.append(f"BT {font} 1 0 0 1 50 {y} Tm ({escape(line)}) Tj ET")
            y -= 15 if bold else 13
            if y < 60:
                y = 750
        y -= 3
    return "\n".join(out).encode("latin-1", "replace")


def build(out_path: Path) -> None:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    n_pages = len(PAGES)
    catalog_id = add(b"")
    pages_id = add(b"")
    f1 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    f2 = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    kids = []
    for lines in PAGES:
        stream = content_stream(lines)
        page_id = add(b"")
        content_id = add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        objects[page_id - 1] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        kids.append(f"{page_id} 0 R")

    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode()
    )

    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(buf))
    print(f"wrote sample PDF: {n_pages} pages, {len(buf)} bytes")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sample_tender.pdf"
    build(target)