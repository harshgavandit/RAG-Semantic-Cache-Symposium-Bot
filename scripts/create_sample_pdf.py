"""Create a clearly fictional, contact-free PDF for trying the public repository."""
import argparse
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

LINES = [
    "Innovate 2026 - Fictional Symposium",
    "This sample document is for testing the RAG application only.",
    "Date: March 15, 2026",
    "Venue: Grand Auditorium and Tech Block, Example Institute",
    "Theme: Bridging Intelligence and Creativity",
    "Eligibility: Undergraduate and postgraduate students with college ID",
    "Registration desk opens at 8:00 AM; register by 8:30 AM.",
    "Inauguration: 9:00 AM to 10:00 AM in the Grand Auditorium",
    "Technical events: Paper Presentation and Code Sprint",
    "Cultural events: Art Showcase and Photography",
    "Lunch break: 1:00 PM to 2:00 PM",
    "Prize distribution: 4:30 PM to 5:30 PM",
    "General registration fee: INR 300 per person",
    "Participation certificates are provided to registered participants.",
    "All event details are fictional; no real contact information is included.",
]


def create_sample(target):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in LINES]
    stream = DecodedStreamObject()
    stream.set_data(("BT /F1 11 Tf 42 745 Td 24 TL\n" + "\n".join(f"({line}) Tj T*" for line in escaped) + "\nET").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/Title": "Fictional symposium sample for RAG testing"})
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as output:
        writer.write(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "symposium.pdf")
    args = parser.parse_args()
    try:
        create_sample(args.output)
    except FileExistsError:
        parser.exit(1, "A document already exists at the output path. Choose --output to preserve it.\n")
    print("Fictional sample PDF created. Run ingestion next.")
