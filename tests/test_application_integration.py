import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model_router import ModelRouter
from services.verifier import CodeVerifier, ComparisonState, VerificationState
from services.verification_pipeline import should_run_response_verifier, verify_generated_answer
from services.verification_ui import status_text

CPP='''#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<n*24; return 0;}'''

class FakeLLM:
    def __init__(self, replies=None, error=None): self.replies=list(replies or []); self.error=error
    def chat(self, **kwargs):
        if self.error: raise self.error
        return self.replies.pop(0) if self.replies else ""

def run(prompt, expected, actual_code=CPP, replies=None):
    router=ModelRouter(["gemma3:4b","qwen2.5-coder:7b"])
    verifier=CodeVerifier(FakeLLM(replies))
    reply=f"```cpp\n{actual_code}\n```"
    return verify_generated_answer(router=router, verifier=verifier, model="qwen2.5-coder:7b", api_messages=[{"role":"user","content":prompt}], prompt=prompt, reply=reply)


def test_A_match_is_verified():
    a=run("For input 5, the expected output is 120.", "120")
    assert a.attempted and a.result.state is VerificationState.VERIFIED and a.result.verified


def test_B_mismatch_is_not_verified():
    a=run("For input 5, the expected output is 121.", "121", replies=[""])
    assert a.result.state is not VerificationState.VERIFIED
    assert a.result.comparison is ComparisonState.MISMATCH
    label,_=status_text(a.result); assert label != "VERIFIED"


def test_C_missing_expected_output_is_not_verified():
    a=run("Write a C++ program. Input: 5", None)
    assert a.result.state is VerificationState.EXECUTED and not a.result.verified


def test_D_no_test_case_is_not_verified():
    a=run("Write a C++ program that multiplies input by 24.", None)
    assert a.result.state is VerificationState.COMPILED and not a.result.verified


def test_E_failed_verifier_is_not_verified():
    a=run("For input 5, the expected output is 120.", "120", actual_code='#include <iostreamm>\nint main(){return 0;}')
    assert a.result.state is VerificationState.FAILED and not a.result.verified


def test_F_verifier_exception_is_not_silently_success():
    class RaisingVerifier(CodeVerifier):
        def _evaluate(self,*args): raise RuntimeError("boom")
    router=ModelRouter(["qwen2.5-coder:7b"])
    verifier=RaisingVerifier(FakeLLM())
    result = verify_generated_answer(router=router, verifier=verifier, model="qwen2.5-coder:7b", api_messages=[], prompt="For input 5, the expected output is 120.", reply=f"```cpp\n{CPP}\n```")
    assert result.result.state is VerificationState.FAILED
    assert result.result.verified is False


def test_G_executable_failure_blocks_response_verifier():
    assert should_run_response_verifier(False, ModelRouter(["gemma3:4b"]), "For input 5, expected output is 121.") is False
    # The actual application condition is represented by this helper: once executable verification ran,
    # the general response verifier is not permitted to override its result.
    assert should_run_response_verifier(True, ModelRouter(["gemma3:4b"]), "What is 2+2?") is False


def test_general_prose_is_not_sent_to_executable_verifier():
    router=ModelRouter(["gemma3:4b"]); verifier=CodeVerifier(FakeLLM())
    a=verify_generated_answer(router=router, verifier=verifier, model="gemma3:4b", api_messages=[], prompt="Explain photosynthesis.", reply="Plants convert light energy into chemical energy.")
    assert a.attempted is False and a.result is None
