import json
from dataclasses import replace
from pathlib import Path
import faiss
import pytest


def make_index(tmp_path, monkeypatch):
    from config import Settings
    from phase2_retrieval import resources
    root = tmp_path
    (root / "data").mkdir()
    (root / "data" / "symposium.pdf").write_bytes(b"fixture-pdf")
    folder = root / "vector_store"
    folder.mkdir()
    import numpy as np
    index = faiss.IndexFlatL2(2)
    index.add(np.array([[1, 0]], dtype="float32"))
    faiss.write_index(index, str(folder / "index.faiss"))
    (folder / "documents.json").write_text(json.dumps([{"text":"Event date: March 15", "metadata":{"page":0}}]))
    manifest = {"embedding_model":"text-embedding-3-small", "dimensions":2,
        "pdf_sha256":resources.file_hash(root / "data" / "symposium.pdf"),
        "index_sha256":resources.file_hash(folder / "index.faiss"),
        "documents_sha256":resources.file_hash(folder / "documents.json")}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(resources, "ROOT", root)
    return resources, Settings.from_env({"OPENAI_API_KEY":"test", "RATE_LIMIT_ENABLED":"false"})


def test_json_index_loads_without_executing_pickle(tmp_path, monkeypatch):
    module, settings = make_index(tmp_path, monkeypatch)
    (tmp_path / "vector_store" / "index.pkl").write_bytes(b"invalid-untrusted-pickle")
    _, store, namespace = module.load_vector_store(settings)
    result = store.similarity_search_by_vector([1, 0], k=1)
    assert result[0].page_content == "Event date: March 15"
    assert result[0].metadata["page"] == 0


@pytest.mark.parametrize("filename", ["data/symposium.pdf", "vector_store/documents.json", "vector_store/index.faiss"])
def test_modified_corpus_or_index_rejected(tmp_path, monkeypatch, filename):
    module, settings = make_index(tmp_path, monkeypatch)
    path = tmp_path / filename
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(RuntimeError, match="re-run ingestion"):
        module.load_vector_store(settings)


def test_embedding_model_mismatch_rejected(tmp_path, monkeypatch):
    module, settings = make_index(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="re-run ingestion"):
        module.load_vector_store(replace(settings, embedding_model="different-model"))
