# C:\RAG\ingest.py
from pathlib import Path
# insert.py (your script above)
import yaml
import sys
from rich import print
from chunker import read_corpus
from embedder import Embedder
from store import get_client, get_collection
from utils_meta import sanitize_metas  # <-- add this import
from sources_config import get_config
CFG = get_config()
print(CFG["domains"])
print(CFG["trusted_sources"][0]["name"])

def main(dry: bool=False):
    settings_path = Path("settings.yaml")
    cfg = yaml.safe_load(settings_path.open("r", encoding="utf-8"))
    current_version = int(cfg.get("corpus_version", 0))
    new_version = current_version + 1
    corpus_dir = "./sample_corpus"
    chunks = read_corpus(corpus_dir)
    print(f"[green]Loaded {len(chunks)} chunks from {corpus_dir}[/green]")

    if dry:
        for c in chunks[:2]:
            print(c.id, list(c.metadata.keys()))
        return

    emb = Embedder(cfg["embedding_model"])
    client = get_client(cfg["persist_directory"])
    col = get_collection(client)

    try:
        client.delete_collection("medical_rag")
    except Exception:
        pass
    col = get_collection(client)

    texts = [c.text for c in chunks]
    raw_metas = []
    for c in chunks:
        meta = dict(c.metadata)
        meta["corpus_version"] = new_version
        raw_metas.append(meta)
    ids   = [c.id for c in chunks]
    vecs  = emb.encode(texts)

    # *** FIX: sanitize metadata before passing to Chroma ***
    metas = sanitize_metas(raw_metas)

    # quick sanity checks
    assert len(texts) == len(metas) == len(ids) == len(vecs), "length mismatch"
    for i, m in enumerate(metas[:3]):
        bad = [k for k,v in m.items() if isinstance(v, (list, dict, set))]
        if bad:
            print(f"[red]Row {i} still has non-primitive fields: {bad}[/red]")

    col.add(
        documents=texts,
        metadatas=metas, # type: ignore
        ids=ids,
        embeddings=[v.tolist() for v in vecs],
    )
    print(f"[green]Indexed {len(ids)} chunks → {cfg['persist_directory']}[/green]")

    cfg["corpus_version"] = new_version
    with settings_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg, fh, sort_keys=False)
    print(f"[blue]Corpus version updated to {new_version}[/blue]")

if __name__ == "__main__":
    main("--dry-run" in sys.argv)