"""Application-level executable verification orchestration.

This module is deliberately small: CodeVerifier remains the authoritative
source of executable verification state, while this layer decides whether the
current answer belongs in that subsystem and preserves the structured result.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .verifier import CodeVerifier, VerificationResult, VerificationState


@dataclass(frozen=True)
class ApplicationVerification:
    attempted: bool
    result: VerificationResult | None
    test_input: str | None = None
    expected_output: str | None = None


def should_verify_executable(router: Any, verifier: CodeVerifier, prompt: str, reply: str) -> bool:
    test_input, expected_output = verifier.extract_test_cases(prompt)
    if test_input is not None or expected_output is not None:
        return True
    return bool(router.looks_like_code(prompt))


def verify_generated_answer(
    *,
    router: Any,
    verifier: CodeVerifier,
    model: str,
    api_messages: list[dict[str, Any]],
    prompt: str,
    reply: str,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    num_ctx: int = 4096,
) -> ApplicationVerification:
    test_input, expected_output = verifier.extract_test_cases(prompt)
    if not should_verify_executable(router, verifier, prompt, reply):
        return ApplicationVerification(False, None, test_input, expected_output)
    try:
        result = verifier.verify(
            model=model,
            api_messages=api_messages,
            reply=reply,
            temperature=temperature,
            max_tokens=max_tokens,
            num_ctx=num_ctx,
            test_input=test_input,
            expected_output=expected_output,
        )
    except Exception as exc:
        result = VerificationResult(
            reply=reply,
            state=VerificationState.FAILED,
            note=f"[WARN] Verification failed internally; the original answer was preserved. ({exc})",
            test_input_present=test_input is not None,
            expected_output_present=expected_output is not None,
        )
    return ApplicationVerification(True, result, test_input, expected_output)


def should_run_response_verifier(executable_verification_attempted: bool, router: Any, prompt: str) -> bool:
    text = (prompt or "").lower()
    explicit_test_case = "expected output" in text or bool(re.search(r"\binput\s*:", text))
    return (
        not executable_verification_attempted
        and not explicit_test_case
        and not router.is_coder(getattr(router, "current_model", None))
        and not router.looks_like_code(prompt)
    )
