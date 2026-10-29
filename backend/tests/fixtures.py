"""Small test files built in code, so no binary fixtures live in the repo."""

import io

from docx import Document


def pdf_bytes(lines: list[str]) -> bytes:
    """A one-page PDF with the given lines of text (Helvetica)."""
    text_ops = (
        "BT /F1 11 Tf 50 750 Td 14 TL "
        + " ".join(
            "(" + line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '"
            for line in lines
        )
        + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(text_ops)} >>\nstream\n{text_ops}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def docx_bytes(lines: list[str]) -> bytes:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


RESUME = [
    "Asha Rao",
    "asha.rao@example.com | +91 98765 43210 | linkedin.com/in/asharao | github.com/asharao",
    "12 Lake Road, Indiranagar, Bengaluru",
    "Senior Backend Engineer, Acme Payments, 2021 - now",
    "Built Python and FastAPI services on PostgreSQL and Kubernetes (k8s).",
    "Backend Engineer, Globex, 2017 - 2021: Kafka, Docker, AWS.",
]
