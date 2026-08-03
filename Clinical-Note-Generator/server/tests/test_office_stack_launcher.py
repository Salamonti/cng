"""Office stack argv builder (no subprocess)."""

import pytest

from server.core.office_stack_launcher import build_office_argv, office_stack_order


def test_office_stack_order_default():
    assert "fastapi" in office_stack_order({})


def test_build_node_argv():
    data = {"fastapi_port": 7860}
    spec = {
        "kind": "node",
        "executable": r"C:\Program Files\nodejs\node.exe",
        "working_dir": r"C:\project-root\PCHost",
        "arguments": ["server.js"],
        "ports": [3000, 3443],
    }
    exe, argv, cwd, ports = build_office_argv("notes_proxy", spec, data)
    assert "node.exe" in exe.replace("/", "\\")
    assert argv[:2] == [exe, "server.js"]
    assert ports == [3000, 3443]


def test_build_uvicorn_clinical_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FASTAPI_PORT", raising=False)
    data = {"fastapi_port": 9090}
    spec = {"kind": "uvicorn_clinical", "working_dir": r"C:\app\Clinical-Note-Generator", "ports": [7860]}
    exe, argv, cwd, ports = build_office_argv("fastapi", spec, data)
    assert "uvicorn" in argv
    assert "9090" in argv
    assert cwd.endswith("Clinical-Note-Generator")
