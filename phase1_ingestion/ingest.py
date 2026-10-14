"""Rebuild a verified local FAISS index from the supplied symposium PDF."""
import json
import sys
from pathlib import Path
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import faiss
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from config import ROOT, Settings
from phase2_retrieval.resources import file_hash


def ingest():
    settings = Settings.from_env()
    pdf = ROOT / "data" / "symposium.pdf"
    documents = PyPDFLoader(str(pdf)).load()
    chunks = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200).split_documents(documents)
    chunks = [doc for doc in chunks if doc.page_content.strip()]
    if not chunks:
        raise RuntimeError("The PDF contains no extractable text")
    for doc in chunks:
        doc.metadata["source"] = "symposium.pdf"
    embeddings = OpenAIEmbeddings(api_key=settings.api_key, model=settings.embedding_model,
        request_timeout=settings.timeout, max_retries=0)
    store = FAISS.from_documents(chunks, embeddings)
    target = ROOT / "vector_store"
    target.mkdir(exist_ok=True)
    faiss.write_index(store.index, str(target / "index.faiss"))
    (target / "documents.json").write_text(json.dumps([
        {"text": doc.page_content, "metadata": doc.metadata} for doc in chunks
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"pdf_sha256": file_hash(pdf), "index_sha256": file_hash(target / "index.faiss"),
        "documents_sha256": file_hash(target / "documents.json"),
        "embedding_model": settings.embedding_model, "dimensions": store.index.d,
        "pages": len(documents), "chunks": len(chunks)}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Ingestion complete: {len(documents)} pages, {len(chunks)} chunks, {store.index.d} dimensions.")


if __name__ == "__main__":
    try:
        ingest()
    except Exception as error:
        print(f"Ingestion failed ({type(error).__name__}). Check configuration, connectivity and provider quota.")
        sys.exit(1)
