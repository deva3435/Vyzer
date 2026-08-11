"""Authoritative local-model routing for Vyzer.

Routing is capability-first and configuration-aware.  The router never silently
substitutes an unrelated model when a requested capability is unavailable.
"""
from __future__ import annotations

import json
import os
import urllib.request


VISION_MARKERS = [
    "llava", "bakllava", "moondream", "llama3.2-vision", "llama4",
    "minicpm-v", "qwen2-vl", "qwen2.5vl", "qwen2.5-vl", "pixtral",
    "granite3.2-vision", "gemma3", "internvl", "phi3.5-vision",
]
CODER_MARKERS = [
    "coder", "codellama", "starcoder", "codegemma", "wizardcoder",
    "deepseek-coder", "devstral", "codestral",
]
REASONING_MODEL_MARKERS = ["deepseek-r1", "qwq"]
GENERAL_EXCLUDE_MARKERS = tuple(CODER_MARKERS + REASONING_MODEL_MARKERS + VISION_MARKERS)

CODE_ACTION_MARKERS = [
    "write code", "write a program", "write a script", "implement", "implementation",
    "build a program", "create a program", "create a script", "debug", "debugging",
    "fix this code", "fix the code", "compile", "run this code", "refactor", "unit test",
    "code review", "coding problem", "programming problem", "algorithm implementation",
    "stack trace", "traceback", "syntax error", "runtime error", "exception in",
]
LANGUAGE_MARKERS = ["python", "java", "c++", "c#", "javascript", "typescript", "golang", "rust", "sql"]
EXPLANATION_MARKERS = [
    "what does this code do", "explain this code", "explain the code", "how does this code work",
    "what is this code doing", "walk me through this code", "explain this snippet",
]
REASONING_REQUEST_MARKERS = [
    "prove", "derive", "analyze", "analyse", "reason", "reasoning", "step by step", "solve",
    "why does", "why is", "compare", "evaluate", "tradeoff", "trade-off", "complex", "logic",
    "mathematical", "math", "probability", "calculate", "calculation", "equation", "percentage",
    "percent", "puzzle", "riddle", "deduce", "deduction", "conditional probability",
]


class ModelRouter:
    def __init__(self, models: list[str] | None):
        self.models = list(models or [])
        self.configured = {
            "general": os.getenv("VYZER_GENERAL_MODEL", "").strip(),
            "coder": os.getenv("VYZER_CODER_MODEL", "").strip(),
            "reasoning": os.getenv("VYZER_REASONING_MODEL", "").strip(),
            "vision": os.getenv("VYZER_VISION_MODEL", "").strip(),
        }

    def refresh(self, models: list[str] | None) -> None:
        self.models = list(models or [])

    @staticmethod
    def capabilities(model: str) -> list[str]:
        if not model:
            return []
        try:
            payload = json.dumps({"model": model}).encode("utf-8")
            req = urllib.request.Request(
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/api/show",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode("utf-8")).get("capabilities", [])
        except Exception:
            return []

    @classmethod
    def supports_vision(cls, model: str) -> bool:
        if not model:
            return False
        caps = cls.capabilities(model)
        return "vision" in caps or any(marker in model.lower() for marker in VISION_MARKERS)

    @staticmethod
    def is_coder(model: str) -> bool:
        return any(marker in (model or "").lower() for marker in CODER_MARKERS)

    @staticmethod
    def is_reasoning_model(model: str) -> bool:
        return any(marker in (model or "").lower() for marker in REASONING_MODEL_MARKERS)

    @staticmethod
    def looks_like_code(prompt: str) -> bool:
        text = (prompt or "").lower()
        if any(marker in text for marker in EXPLANATION_MARKERS):
            return False
        if any(marker in text for marker in CODE_ACTION_MARKERS):
            return True
        if "```" in text and any(marker in text for marker in LANGUAGE_MARKERS):
            return True
        if any(marker in text for marker in LANGUAGE_MARKERS) and any(
            verb in text for verb in ("program", "function", "script", "class", "application", "solution")
        ):
            return True
        return False

    @staticmethod
    def looks_like_reasoning(prompt: str) -> bool:
        text = (prompt or "").lower()
        return any(marker in text for marker in REASONING_REQUEST_MARKERS)

    @staticmethod
    def looks_like_hard_reasoning(prompt: str) -> bool:
        text = (prompt or "").lower()
        return any(marker in text for marker in [
            "prove", "prove or disprove", "derive", "solve this puzzle", "logic puzzle", "riddle",
            "is it possible to guarantee", "under these constraints", "must satisfy", "complexity proof",
            "formal reasoning", "time complexity", "space complexity", "big-o", "o(n)", "o(1)",
            "impossible", "proof",
        ])

    @staticmethod
    def generation_profile(prompt: str, model: str, has_image: bool = False) -> dict[str, float | int]:
        text = (prompt or "").lower()
        if has_image:
            return {"temperature": 0.4, "max_tokens": 3072}
        if ModelRouter.looks_like_code(text):
            return {"temperature": 0.2, "max_tokens": 4096}
        if ModelRouter.looks_like_hard_reasoning(text):
            return {"temperature": 0.25, "max_tokens": 4096}
        long_form = any(x in text for x in [
            "in detail", "explain in detail", "comprehensive", "university level", "academic", "essay",
            "discuss", "compare and contrast", "thoroughly", "teach me", "research design", "procedure",
            "multiple parts", "from the beginning",
        ])
        reasoning = ModelRouter.looks_like_reasoning(text)
        if long_form and reasoning:
            return {"temperature": 0.25, "max_tokens": 4096}
        if long_form:
            return {"temperature": 0.4, "max_tokens": 4096}
        if reasoning:
            return {"temperature": 0.25, "max_tokens": 4096}
        return {"temperature": 0.45, "max_tokens": 2048}

    def _configured_available(self, kind: str) -> str | None:
        value = self.configured.get(kind, "")
        if value and value in self.models:
            return value
        return None

    def best_vision(self) -> str | None:
        configured = self._configured_available("vision")
        if configured:
            return configured
        for model in self.models:
            if any(marker in model.lower() for marker in ("qwen2.5vl", "qwen2-vl", "minicpm", "llava")):
                return model
        for model in self.models:
            if self.supports_vision(model):
                return model
        return None

    def best_coder(self) -> str | None:
        configured = self._configured_available("coder")
        if configured:
            return configured
        for model in self.models:
            if "qwen2.5-coder" in model.lower():
                return model
        return next((m for m in self.models if self.is_coder(m)), None)

    def best_reasoning(self) -> str | None:
        configured = self._configured_available("reasoning")
        if configured:
            return configured
        for model in self.models:
            if "deepseek-r1" in model.lower():
                return model
        return next((m for m in self.models if self.is_reasoning_model(m)), None)

    def best_general(self) -> str | None:
        configured = self._configured_available("general")
        if configured:
            return configured
        for model in self.models:
            name = model.lower()
            if "gemma3" in name and not self.is_coder(model):
                return model
        for model in self.models:
            name = model.lower()
            if not any(marker in name for marker in GENERAL_EXCLUDE_MARKERS):
                return model
        return None

    def choose_image_answer_model(self, extracted_text: str) -> str | None:
        if self.looks_like_code(extracted_text):
            coder = self.best_coder()
            if coder:
                return coder
        if self.looks_like_hard_reasoning(extracted_text):
            reasoning = self.best_reasoning()
            if reasoning:
                return reasoning
        return self.best_general() or self.best_vision()

    def choose_model(self, selected_model: str | None, prompt: str, has_image: bool = False) -> str | None:
        if self.looks_like_hard_reasoning(prompt):
            model = self.best_reasoning()
            if model:
                return model
        if self.looks_like_code(prompt):
            model = self.best_coder()
            if model:
                return model
        if has_image:
            model = self.best_vision()
            if model:
                return model
        model = self.best_general()
        if model:
            return model
        if selected_model and selected_model in self.models:
            if not self.is_coder(selected_model) and not self.is_reasoning_model(selected_model) and not self.supports_vision(selected_model):
                return selected_model
        return None
