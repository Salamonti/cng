"""Tests for deriving env URLs from llama_instances + feature_routing."""

from server.core.service_endpoints_sync import sync_env_from_structure, validate_llama_instances


def test_sync_env_from_structure_sets_urls():
    data = {
        "fastapi_port": 7860,
        "llama_instances": {
            "a": {"base_url": "http://127.0.0.1:8081"},
            "b": {"base_url": "http://127.0.0.1:8090"},
        },
        "feature_routing": {
            "note_gen": {"primary": "a", "fallback": ""},
            "ocr": {"primary": "b", "fallback": "a"},
        },
        "services_urls": {"rag": "http://127.0.0.1:8007"},
        "env": {},
        "pchost": {},
    }
    sync_env_from_structure(data)
    assert data["env"]["NOTEGEN_URL_PRIMARY"] == "http://127.0.0.1:8081"
    assert data["env"]["NOTEGEN_URL_FALLBACK"] == ""
    assert data["env"]["OCR_URL_PRIMARY"] == "http://127.0.0.1:8090"
    assert data["env"]["OCR_URL_FALLBACK"] == "http://127.0.0.1:8081"
    assert data["env"]["RAG_URL"] == "http://127.0.0.1:8007"
    assert data["pchost"]["backend_url"] == "http://127.0.0.1:7860"


def test_sync_skips_when_no_llama_instances():
    data = {"env": {"NOTEGEN_URL_PRIMARY": "http://keep.example:1"}, "llama_instances": {}}
    sync_env_from_structure(data)
    assert data["env"]["NOTEGEN_URL_PRIMARY"] == "http://keep.example:1"


def test_sync_env_from_structure_sets_asr_urls_from_all_whisper_instances():
    data = {
        "whisper_instances": {
            "primary": {"base_url": "http://127.0.0.1:8095"},
            "fallback": {"base_url": "http://127.0.0.1:8096"},
            "third": {"base_url": "http://127.0.0.1:8097/"},
        },
        "env": {},
    }
    sync_env_from_structure(data)
    assert data["env"]["ASR_URL"] == "http://127.0.0.1:8095"
    assert data["env"]["ASR_URL_FALLBACK"] == "http://127.0.0.1:8096"
    assert data["env"]["ASR_URLS"] == (
        "http://127.0.0.1:8095,"
        "http://127.0.0.1:8096,"
        "http://127.0.0.1:8097"
    )


def test_validate_unknown_instance():
    data = {
        "llama_instances": {"only": {"base_url": "http://x"}},
        "feature_routing": {"note_gen": {"primary": "missing", "fallback": ""}},
    }
    w = validate_llama_instances(data)
    assert any("unknown llama_instances" in x for x in w)


def test_validate_clean():
    data = {
        "llama_instances": {"only": {"base_url": "http://x"}},
        "feature_routing": {"note_gen": {"primary": "only", "fallback": ""}},
    }
    assert validate_llama_instances(data) == []
