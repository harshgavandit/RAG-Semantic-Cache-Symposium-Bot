"""Build only the fictional sample into isolated deploy_data; never read private PDF."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import ROOT, Settings
from scripts.create_sample_pdf import create_sample
from phase1_ingestion.ingest import ingest


def build():
    s = Settings.from_env()
    target = ROOT / "deploy_data"
    pdf = target / "data" / "symposium.pdf"
    if not pdf.exists():
        create_sample(pdf)
    ingest(s, target)


if __name__ == "__main__":
    build()
