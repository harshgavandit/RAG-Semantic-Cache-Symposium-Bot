"""Load the FAISS index with JSON metadata; never deserialize index.pkl."""
import hashlib
import json
import faiss
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain_community.docstore.in_memory import InMemoryDocstore
from langchain_core.documents import Document
from config import ROOT


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_vector_store(settings):
    path = ROOT / "vector_store"
    if not (path / "manifest.json").exists():
        raise RuntimeError("Run python -m phase1_ingestion.ingest to build the verified index first.")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if (manifest["embedding_model"] != settings.embedding_model
            or manifest["pdf_sha256"] != file_hash(ROOT / "data" / "symposium.pdf")
            or manifest["index_sha256"] != file_hash(path / "index.faiss")
            or manifest["documents_sha256"] != file_hash(path / "documents.json")):
        raise RuntimeError("Vector index, document or embedding model changed; re-run ingestion.")
    items = json.loads((path / "documents.json").read_text(encoding="utf-8"))
    docs = {str(i): Document(page_content=item["text"], metadata=item["metadata"]) for i, item in enumerate(items)}
    index = faiss.read_index(str(path / "index.faiss"))
    if index.ntotal != len(docs) or index.d != manifest["dimensions"]:
        raise RuntimeError("Vector index metadata is inconsistent; re-run ingestion.")
    embeddings = OpenAIEmbeddings(api_key=settings.api_key, model=settings.embedding_model,
        request_timeout=settings.timeout, max_retries=0)
    store = FAISS(embeddings, index, InMemoryDocstore(docs), {i: str(i) for i in range(len(docs))})
    namespace = hashlib.sha256((manifest["pdf_sha256"] + manifest["index_sha256"]
        + settings.embedding_model + settings.model + str(settings.cache_threshold) + "prompt-v2").encode()).hexdigest()[:24]
    return embeddings, store, namespace


def load_resources(settings):
    embeddings, store, namespace = load_vector_store(settings)
    llm = ChatOpenAI(api_key=settings.api_key, model=settings.model, temperature=0,
        timeout=settings.timeout, max_retries=0, max_completion_tokens=settings.max_tokens)
    return embeddings, store, llm, namespace
