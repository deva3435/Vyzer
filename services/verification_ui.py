"""Structured rendering helpers for executable verification status."""
from __future__ import annotations

from .verifier import ComparisonState, VerificationResult, VerificationState


def status_text(result: VerificationResult) -> tuple[str, str]:
    """Return (label, diagnostic) without inferring state from prose."""
    if result.state is VerificationState.VERIFIED:
        return "VERIFIED", "Runtime output matched the supplied expected output."
    if result.state is VerificationState.EXECUTED:
        if result.comparison is ComparisonState.MISMATCH:
            return "NOT VERIFIED", "The executed output did not match the supplied expected output."
        return "EXECUTED", "The program executed, but correctness was not established."
    if result.state is VerificationState.COMPILED:
        return "COMPILED", "The program compiled, but runtime correctness was not established."
    if result.state is VerificationState.ENVIRONMENT_UNAVAILABLE:
        return "UNAVAILABLE", "The required execution toolchain is unavailable."
    if result.state is VerificationState.FAILED:
        return "FAILED", "Verification could not establish correctness."
    if result.state is VerificationState.NOT_APPLICABLE:
        return "NOT APPLICABLE", "No complete executable answer required verification."
    return result.state.value, result.note or "Verification was not established."
