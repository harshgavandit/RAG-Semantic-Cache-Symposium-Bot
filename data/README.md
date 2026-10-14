# Local source document

Source PDFs are ignored by Git. To try the public repository, run:

```bash
python scripts/create_sample_pdf.py
python -m phase1_ingestion.ingest
```

The generator creates a clearly fictional symposium document with no phone numbers or email addresses. It refuses to overwrite an existing PDF.

You can instead place a document you are authorized to process at `data/symposium.pdf`, then run ingestion. OpenAI receives its text to generate embeddings and answer questions.
