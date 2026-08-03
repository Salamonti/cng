"""Start all AI servers (llama + whisper) from service_endpoints.json."""
import json, sys, time
from pathlib import Path

REPO = Path(r"C:\project-root")
sys.path.insert(0, str(REPO / "Clinical-Note-Generator"))

from server.core.ai_process_launcher import start_instance, tcp_port_listening, parse_port_from_url
from server.core.service_endpoints import load_service_endpoints

def main():
    data = load_service_endpoints() or {}
    
    instances = [
        ("llama_instances", "note_primary", "note LLM (Ministral-3-14B)", 8081),
        ("llama_instances", "ocr_primary", "OCR LLM (olmOCR-2-7B)", 8090),
        ("llama_instances", "rag_comment_primary", "RAG Comment LLM (Puzzle-88B)", 8036),
        ("llama_instances", "gemma_4", "Gemma-4 (26B MoE)", 8037),
        ("whisper_instances", "primary", "Whisper primary", 8095),
        ("whisper_instances", "fallback", "Whisper fallback", 8096),
    ]
    
    launched = []
    failed = []
    
    for group, inst_id, label, port in instances:
        if tcp_port_listening("127.0.0.1", port):
            print(f"  [SKIP] {label} (:{port}) — already listening")
            launched.append((label, port))
            continue
        
        print(f"  [START] {label} (:{port})...", end=" ", flush=True)
        try:
            ok, msg, pid = start_instance(data, group, inst_id)
            if ok:
                print(f"OK (pid={pid})")
                launched.append((label, port))
            else:
                print(f"FAILED: {msg}")
                failed.append((label, port, msg))
        except Exception as e:
            print(f"ERROR: {e}")
            failed.append((label, port, str(e)))
    
    print(f"\nLaunched: {len(launched)}, Failed: {len(failed)}")
    
    # Wait for servers to initialize
    if launched:
        wait = 20 if len([x for x in launched if x[0] not in ("previous",)]) <= 3 else 40
        print(f"\nWaiting {wait}s for model loading...")
        time.sleep(wait)
    
    # Final check
    print("\n=== STATUS ===")
    for label, port in launched:
        up = tcp_port_listening("127.0.0.1", port)
        print(f"  {label:40s} :{port}  {'UP' if up else 'DOWN'}")
    for label, port, msg in failed:
        print(f"  {label:40s} :{port}  FAILED: {msg[:120]}")
    
    if failed:
        print("\nFAILED instances:", len(failed))
        sys.exit(1)
    else:
        print("\nAll AI servers started.")

if __name__ == "__main__":
    main()
