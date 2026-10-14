import subprocess
import sys
from pathlib import Path
from pypdf import PdfReader


def test_sample_pdf_extracts_and_does_not_overwrite(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "create_sample_pdf.py"
    target = tmp_path / "sample.pdf"
    first = subprocess.run([sys.executable, str(script), "--output", str(target)], capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    text = "\n".join(page.extract_text() for page in PdfReader(target).pages)
    assert "March 15, 2026" in text
    assert "sample document" in text
    assert "+91" not in text and "@" not in text
    before = target.read_bytes()
    second = subprocess.run([sys.executable, str(script), "--output", str(target)], capture_output=True, text=True)
    assert second.returncode != 0
    assert target.read_bytes() == before
