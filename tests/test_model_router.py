import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_router import ModelRouter


def router():
    return ModelRouter(["gemma3:4b", "qwen2.5-coder:7b", "deepseek-r1:7b", "qwen2.5vl:7b"])


def test_coding_routes_to_coder():
    r=router(); assert r.choose_model("gemma3:4b", "Write a C++ program that reads n and prints n squared") == "qwen2.5-coder:7b"


def test_general_routes_to_general():
    r=router(); assert r.choose_model("qwen2.5-coder:7b", "What causes seasons on Earth?") == "gemma3:4b"


def test_hard_reasoning_routes_to_reasoning():
    r=router(); assert r.choose_model("gemma3:4b", "Prove that this algorithm is correct") == "deepseek-r1:7b"


def test_code_explanation_is_not_executable_code_request():
    r=router(); assert r.looks_like_code("What does this code do? ```python\nprint(1)\n```") is False


def test_unavailable_capability_does_not_silently_use_coder_for_general():
    r=ModelRouter(["qwen2.5-coder:7b"])
    assert r.choose_model("qwen2.5-coder:7b", "What is photosynthesis?") is None


def test_explicit_model_configuration_is_authoritative_when_available(monkeypatch):
    monkeypatch.setenv("VYZER_GENERAL_MODEL", "gemma3:4b")
    r=router(); assert r.best_general() == "gemma3:4b"


def test_ui_model_label_uses_actual_response_model():
    from services.model_display import actual_model_label
    assert actual_model_label({"model": "qwen2.5-coder:7b"}, "gemma3:4b") == "qwen2.5-coder:7b"
    assert actual_model_label({}, "gemma3:4b") == "gemma3:4b"
