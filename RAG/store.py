# C:\RAG\store.py
import os
import threading
import chromadb
from chromadb.config import Settings

_CLIENT_LOCK = threading.Lock()
_CLIENT = None
_CLIENT_PATH: str | None = None

_COLLECTION_LOCK = threading.Lock()
_COLLECTION_CACHE: dict[tuple[int, str], any] = {}


def get_client(persist_directory: str):
    global _CLIENT, _CLIENT_PATH
    abs_path = os.path.abspath(persist_directory)
    os.makedirs(abs_path, exist_ok=True)
    os.environ.setdefault("BM25_PERSIST_DIR", abs_path)

    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT_PATH != abs_path:
            _CLIENT = chromadb.PersistentClient(
                path=abs_path,
                settings=Settings(anonymized_telemetry=False)
            )
            _CLIENT_PATH = abs_path
    return _CLIENT


def get_collection(client, name="medical_rag"):
    key = (id(client), name)
    with _COLLECTION_LOCK:
        if key not in _COLLECTION_CACHE:
            _COLLECTION_CACHE[key] = client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"}
            )
        return _COLLECTION_CACHE[key]
