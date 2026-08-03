"""Build argv for native AI servers (no subprocess spawn in CI)."""

from server.core.ai_process_launcher import build_argv_for_instance, parse_port_from_url


def test_parse_port():
    assert parse_port_from_url("http://127.0.0.1:8081") == 8081
    assert parse_port_from_url("http://127.0.0.1:8081/v1") == 8081
    assert parse_port_from_url("") is None


def test_build_llama_auto_argv():
    data = {
        "ai_binary_defaults": {"llama_server": r"C:\llama\llama-server.exe"},
        "llama_instances": {
            "x": {
                "kind": "llama",
                "base_url": "http://127.0.0.1:8081",
                "model": r"C:\models\m.gguf",
                "mmproj": "",
                "sampler": {"temp": 0.1, "min_p": 0.05, "top_p": 0.8, "top_k": 40},
                "launch": {"env": {"CUDA_VISIBLE_DEVICES": "0"}},
            }
        },
    }
    exe, argv, env, cwd, logf = build_argv_for_instance(data, "llama_instances", "x")
    assert exe == r"C:\llama\llama-server.exe"
    assert argv[0] == exe
    assert "-m" in argv
    assert r"C:\models\m.gguf" in argv
    assert "--port" in argv
    assert "8081" in argv
    assert env.get("CUDA_VISIBLE_DEVICES") == "0"
    assert "--mmap" in argv
    assert "--direct-io" in argv
    assert "--host" in argv and "0.0.0.0" in argv


def test_build_llama_bind_host_and_no_mmap():
    data = {
        "ai_binary_defaults": {"llama_server": r"C:\llama\llama-server.exe"},
        "llama_instances": {
            "x": {
                "kind": "llama",
                "base_url": "http://127.0.0.1:8081",
                "model": r"C:\models\m.gguf",
                "bind_host": "127.0.0.1",
                "mmap": False,
                "sampler": {},
                "launch": {},
            }
        },
    }
    _, argv, _, _, _ = build_argv_for_instance(data, "llama_instances", "x")
    assert "127.0.0.1" in argv
    assert "--no-mmap" in argv


def test_build_explicit_arguments_override():
    data = {
        "llama_instances": {
            "x": {
                "kind": "custom",
                "base_url": "http://127.0.0.1:9999",
                "model": "",
                "launch": {
                    "executable": r"C:\bin\llama-server.exe",
                    "arguments": ["--help"],
                },
            }
        },
    }
    exe, argv, _, _, _ = build_argv_for_instance(data, "llama_instances", "x")
    assert argv[:2] == [r"C:\bin\llama-server.exe", "--help"]


def test_build_whisper_auto_argv():
    data = {
        "ai_binary_defaults": {"whisper_server": r"C:\w\whisper-server.exe"},
        "whisper_instances": {
            "primary": {
                "kind": "whisper",
                "base_url": "http://127.0.0.1:8095",
                "model": r"C:\models\w.bin",
                "launch": {},
            }
        },
    }
    exe, argv, _, _, _ = build_argv_for_instance(data, "whisper_instances", "primary")
    assert exe == r"C:\w\whisper-server.exe"
    assert "-m" in argv and r"C:\models\w.bin" in argv
    assert "8095" in argv


def test_whisper_sync_overrides_asr():
    from server.core.service_endpoints_sync import sync_env_from_structure

    data = {
        "llama_instances": {},
        "services_urls": {"asr_primary": "http://127.0.0.1:1"},
        "whisper_instances": {
            "primary": {"base_url": "http://127.0.0.1:8095"},
            "fallback": {"base_url": "http://127.0.0.1:8096"},
        },
        "env": {},
    }
    sync_env_from_structure(data)
    assert data["env"]["ASR_URL"] == "http://127.0.0.1:8095"
    assert data["env"]["ASR_URL_FALLBACK"] == "http://127.0.0.1:8096"
