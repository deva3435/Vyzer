from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.verifier import CodeVerifier, ComparisonState, VerificationState


class FakeLLM:
    def __init__(self, replies=None, error=None):
        self.replies = list(replies or [])
        self.error = error
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.replies.pop(0) if self.replies else ""


CPP_FACTORIAL = r'''#include <iostream>
using namespace std;
int main() {
    int n; cin >> n;
    long long result = 1;
    for (int i = 2; i <= n; ++i) result *= i;
    cout << result << endl;
    return 0;
}'''


def verify(prompt, reply, llm=None):
    verifier = CodeVerifier(llm or FakeLLM(), timeout=5, max_attempts=2)
    test_input, expected = verifier.extract_test_cases(prompt)
    return verifier.verify(
        model="fake-model",
        api_messages=[{"role": "user", "content": prompt}],
        reply=reply,
        test_input=test_input,
        expected_output=expected,
    )


def fenced(code, language="cpp"):
    return f"```{language}\n{code}\n```"


def test_correct_factorial_120_is_verified():
    result = verify("For input 5, the expected output is 120.", fenced(CPP_FACTORIAL))
    assert result.state is VerificationState.VERIFIED
    assert result.comparison is ComparisonState.MATCH
    assert result.verified is True


def test_wrong_expected_121_never_verifies():
    result = verify("For input 5, the expected output is 121.", fenced(CPP_FACTORIAL), FakeLLM([fenced(CPP_FACTORIAL)]))
    assert result.state is not VerificationState.VERIFIED
    assert result.comparison is ComparisonState.MISMATCH
    assert result.verified is False


def test_prompt_contaminated_output_is_not_verified():
    code = CPP_FACTORIAL.replace("cout << result << endl;", 'cout << "Enter an integer: " << result << endl;')
    result = verify("For input 5, the expected output is 120.", fenced(code), FakeLLM([fenced(CPP_FACTORIAL)]))
    assert result.state is VerificationState.VERIFIED
    assert result.comparison is ComparisonState.MATCH
    assert result.repaired is True


def test_label_contaminated_output_is_not_verified():
    code = CPP_FACTORIAL.replace("cout << result << endl;", 'cout << "Factorial of 5 is: " << result << endl;')
    result = verify("For input 5, the expected output is 120.", fenced(code), FakeLLM([fenced(CPP_FACTORIAL)]))
    assert result.state is VerificationState.VERIFIED
    assert result.repaired is True



def test_prompt_contamination_without_repair_never_verifies():
    code = CPP_FACTORIAL.replace("cout << result << endl;", 'cout << "Enter an integer: " << result << endl;')
    verifier = CodeVerifier(FakeLLM(), timeout=5, max_attempts=1)
    result = verifier.verify(model="fake", api_messages=[], reply=fenced(code), test_input="5", expected_output="120")
    assert result.state is VerificationState.FAILED
    assert result.comparison is ComparisonState.MISMATCH
    assert result.verified is False


def test_label_contamination_without_repair_never_verifies():
    code = CPP_FACTORIAL.replace("cout << result << endl;", 'cout << "Factorial of 5 is: " << result << endl;')
    verifier = CodeVerifier(FakeLLM(), timeout=5, max_attempts=1)
    result = verifier.verify(model="fake", api_messages=[], reply=fenced(code), test_input="5", expected_output="120")
    assert result.state is VerificationState.FAILED
    assert result.comparison is ComparisonState.MISMATCH
    assert result.verified is False

def test_input_without_expected_output_is_executed_not_verified():
    result = verify("Write a C++ factorial program. Input: 5", fenced(CPP_FACTORIAL))
    assert result.state is VerificationState.EXECUTED
    assert result.comparison is ComparisonState.UNAVAILABLE
    assert result.verified is False


def test_no_test_case_is_compile_only_not_verified():
    result = verify("Write a C++ factorial program.", fenced(CPP_FACTORIAL))
    assert result.state is VerificationState.COMPILED
    assert result.verified is False


def test_expected_output_without_input_does_not_invent_input():
    result = verify("Write a C++ factorial program. The expected output is 120.", fenced(CPP_FACTORIAL))
    assert result.state is VerificationState.COMPILED
    assert result.test_input_present is False
    assert result.verified is False


def test_broken_cpp_repair_is_compiled_and_tested():
    broken = '''#include <iostreamm>\nusing namespace std;\nint main() { int a, b; cin >> a >> b; cout << a + b << endl; return 0; }'''
    corrected = '''#include <iostream>\nusing namespace std;\nint main() { int a, b; cin >> a >> b; cout << a + b << endl; return 0; }'''
    result = verify("Input: 2 3, Output: 5", fenced(broken), FakeLLM([fenced(corrected)]))
    assert result.state is VerificationState.VERIFIED
    assert result.repaired is True


def test_multiple_code_blocks_prefers_complete_main():
    fragment = "for (int i = 0; i < n; i++) { ... }"
    reply = f"""Here is a fragment:\n```cpp\n{fragment}\n```\nAnd the complete program:\n```cpp\n{CPP_FACTORIAL}\n```"""
    language, code = CodeVerifier.extract_code(reply)
    assert language == "cpp"
    assert "main(" in code


def test_unfenced_complete_cpp_is_extracted():
    language, code = CodeVerifier.extract_code(CPP_FACTORIAL)
    assert language == "cpp"
    assert "main" in code


def test_fragment_only_repair_is_rejected():
    fragment = "for (int i = 0; i < n; i++) {\n    ...\n}"
    result = verify("For input 5, the expected output is 120.", fenced("#include <iostream>\nint main(){return 0;}"), FakeLLM([fragment, fragment]))
    assert result.state is not VerificationState.VERIFIED
    assert result.verified is False


def test_compiler_diagnostic_is_source_failure_not_environment():
    broken = '#include <iostreamm>\nint main(){return 0;}'
    result = verify("Write a C++ program.", fenced(broken), FakeLLM([]))
    assert result.state is VerificationState.FAILED
    assert result.state is not VerificationState.ENVIRONMENT_UNAVAILABLE
    assert "iostreamm" in result.stderr


def test_runtime_timeout_fails_closed():
    code = "while True: pass"
    result = CodeVerifier(FakeLLM(), timeout=1).verify(
        model="fake", api_messages=[], reply=fenced(code, "python"), test_input="", expected_output="1"
    )
    assert result.state is VerificationState.FAILED
    assert result.verified is False


def test_repair_failure_preserves_original_answer():
    original = fenced("#include <iostream>\nint main(){std::cout << 1;}")
    result = verify("For input 5, the expected output is 120.", original, FakeLLM(["not code"]))
    assert result.state is VerificationState.FAILED
    assert result.reply == original
    assert result.verified is False


def test_empty_repair_response_fails_closed():
    original = fenced("#include <iostream>\nint main(){std::cout << 1;}")
    result = verify("For input 5, the expected output is 120.", original, FakeLLM([""]))
    assert result.state is VerificationState.FAILED
    assert result.verified is False


def test_repair_wrong_output_never_verifies():
    original = fenced("#include <iostream>\nint main(){std::cout << 1;}")
    wrong_repair = fenced("#include <iostream>\nint main(){std::cout << 119;}")
    result = verify("For input 5, the expected output is 120.", original, FakeLLM([wrong_repair]))
    assert result.state is not VerificationState.VERIFIED
    assert result.verified is False


def test_python_execution_is_verified():
    code = "import sys\nn=int(sys.stdin.readline())\nprint(n*n)"
    result = verify("For input 5, the expected output is 25.", fenced(code, "python"))
    assert result.state is VerificationState.VERIFIED
    assert result.comparison is ComparisonState.MATCH


def test_c_execution_is_verified():
    code = '#include <stdio.h>\nint main(){int n; scanf("%d", &n); printf("%d\\n", n*n);}'
    result = verify("For input 5, the expected output is 25.", fenced(code, "c"))
    assert result.state is VerificationState.VERIFIED


def test_java_execution_is_verified():
    code = 'import java.util.*; public class Main { public static void main(String[] a){ Scanner s=new Scanner(System.in); int n=s.nextInt(); System.out.println(n*n); }}'
    result = verify("For input 5, the expected output is 25.", fenced(code, "java"))
    assert result.state is VerificationState.VERIFIED


def test_javascript_execution_is_verified():
    code = 'const fs=require("fs"); const n=Number(fs.readFileSync(0,"utf8")); console.log(n*n);'
    result = verify("For input 5, the expected output is 25.", fenced(code, "javascript"))
    assert result.state is VerificationState.VERIFIED


def test_typescript_execution_is_verified():
    code = 'declare const require: any; const fs:any=require("fs"); const n:number=Number(fs.readFileSync(0,"utf8")); console.log(n*n);'
    result = verify("For input 5, the expected output is 25.", fenced(code, "typescript"))
    assert result.state is VerificationState.VERIFIED


def test_go_execution_is_verified():
    code = 'package main\nimport "fmt"\nfunc main(){var n int; fmt.Scan(&n); fmt.Println(n*n)}'
    result = verify("For input 5, the expected output is 25.", fenced(code, "go"))
    assert result.state is VerificationState.VERIFIED


def test_rust_execution_is_verified():
    import shutil
    if shutil.which("rustc") is None:
        import pytest
        pytest.skip("rustc is not installed in this environment")
    code = 'use std::io::{self, Read}; fn main(){let mut s=String::new(); io::stdin().read_to_string(&mut s).unwrap(); let n:i32=s.trim().parse().unwrap(); println!("{}", n*n);}'
    result = verify("For input 5, the expected output is 25.", fenced(code, "rust"))
    assert result.state is VerificationState.VERIFIED


def test_missing_cpp_toolchain_is_environment_unavailable(monkeypatch):
    verifier = CodeVerifier(FakeLLM())
    monkeypatch.setattr("services.verifier.shutil.which", lambda name: None if name == "g++" else "/usr/bin/tool")
    result = verifier.verify(model="fake", api_messages=[], reply=fenced(CPP_FACTORIAL), test_input="5", expected_output="120")
    assert result.state is VerificationState.ENVIRONMENT_UNAVAILABLE
    assert result.verified is False


def test_compilation_timeout_fails_closed(monkeypatch):
    verifier = CodeVerifier(FakeLLM(), timeout=1)
    real_run = __import__("subprocess").run
    def timeout_run(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if command and command[0] == "g++":
            raise __import__("subprocess").TimeoutExpired(command, 1)
        return real_run(*args, **kwargs)
    monkeypatch.setattr("services.verifier.subprocess.run", timeout_run)
    result = verifier.verify(model="fake", api_messages=[], reply=fenced(CPP_FACTORIAL), test_input="5", expected_output="120")
    assert result.state is VerificationState.FAILED
    assert result.verified is False
