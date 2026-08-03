"""Vision QA client: multi-turn payload (image only on first user message)."""

from server.services.vision_qa_client import VisionQAEngine


def test_multi_turn_payload_image_only_first_user(monkeypatch):
    monkeypatch.setenv("LLM_QA_VISION_URL", "http://127.0.0.1:9")
    eng = VisionQAEngine(url="")
    payload = eng._build_multi_turn_payload(
        "AAA",
        "image/png",
        [
            {"q": "What is this?", "a": "Prior answer about the image.", "channel": "vision"},
            {"q": "Any red flags?", "a": "Second assistant reply.", "channel": "vision"},
        ],
        "What else should I check?",
        stream=True,
    )
    msgs = payload["messages"]
    assert len(msgs) == 5
    assert msgs[0]["role"] == "user"
    assert isinstance(msgs[0]["content"], list)
    assert msgs[0]["content"][0]["type"] == "image_url"
    assert msgs[1]["role"] == "assistant"
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"] != msgs[0]["content"]
    assert isinstance(msgs[2]["content"], str)
    assert msgs[3]["role"] == "assistant"
    assert msgs[4]["role"] == "user"
    assert "What else should I check?" in msgs[4]["content"]


def test_single_turn_when_no_history(monkeypatch):
    monkeypatch.setenv("LLM_QA_VISION_URL", "http://127.0.0.1:9")
    eng = VisionQAEngine(url="")
    payload = eng._build_multi_turn_payload(
        "AAA",
        "image/jpeg",
        [],
        "Describe the lesion.",
        stream=True,
    )
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
