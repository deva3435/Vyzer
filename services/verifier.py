"""Authoritative, fail-closed executable-code verification for Vyzer."""
from __future__ import annotations

import ast
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class VerificationState(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    GENERATED = "GENERATED"
    COMPILED = "COMPILED"
    EXECUTED = "EXECUTED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    ENVIRONMENT_UNAVAILABLE = "ENVIRONMENT_UNAVAILABLE"


class ComparisonState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class VerificationResult:
    reply: str
    state: VerificationState
    language: str | None = None
    note: str = ""
    stdout: str = ""
    stderr: str = ""
    comparison: ComparisonState = ComparisonState.UNAVAILABLE
    repaired: bool = False
    attempts: int = 0
    test_input_present: bool = False
    expected_output_present: bool = False

    @property
    def verified(self) -> bool:
        return self.state is VerificationState.VERIFIED

    @property
    def success(self) -> bool:
        return self.state in {
            VerificationState.COMPILED,
            VerificationState.EXECUTED,
            VerificationState.VERIFIED,
        }


class CodeVerifier:
    SUPPORTED = {"python", "c", "cpp", "java", "javascript", "typescript", "go", "rust"}
    COMPILED = {"c", "cpp", "java", "typescript", "go", "rust"}
    MAIN_REQUIRED = {"c", "cpp", "java", "go", "rust"}

    def __init__(self, llm, timeout: int = 8, max_attempts: int = 3):
        self.llm = llm
        self.timeout = max(1, int(timeout))
        self.max_attempts = max(1, int(max_attempts))

    @staticmethod
    def normalize_language(language: str) -> str:
        aliases = {
            "py": "python", "python3": "python",
            "cpp": "cpp", "c++": "cpp", "cc": "cpp", "cxx": "cpp",
            "js": "javascript", "jsx": "javascript", "node": "javascript", "nodejs": "javascript",
            "ts": "typescript", "tsx": "typescript",
            "golang": "go", "rs": "rust",
        }
        value = (language or "").strip().lower()
        return aliases.get(value, value)

    @staticmethod
    def _is_complete(language: str, code: str) -> bool:
        if not code.strip():
            return False
        if language in CodeVerifier.MAIN_REQUIRED:
            patterns = {
                "c": r"\bint\s+main\s*\(",
                "cpp": r"\b(?:int\s+)?main\s*\(",
                "java": r"\b(?:public\s+)?static\s+void\s+main\s*\(",
                "go": r"\bfunc\s+main\s*\(",
                "rust": r"\bfn\s+main\s*\(",
            }
            if not re.search(patterns[language], code):
                return False
            if code.count("{") != code.count("}"):
                return False
        if language == "python":
            try:
                tree = ast.parse(code)
            except SyntaxError:
                return False
            return bool(tree.body)
        if language in {"javascript", "typescript"}:
            return bool(re.search(r"\b(?:console\.log|function|const|let|var|class|import|export)\b", code))
        return True

    @classmethod
    def _score(cls, language: str, code: str, labelled: bool) -> int:
        score = 20 if labelled else 0
        if cls._is_complete(language, code):
            score += 100
        if language in cls.MAIN_REQUIRED and re.search(r"\bmain\s*\(", code):
            score += 30
        if len(code) > 80:
            score += 10
        if code.count("{") == code.count("}"):
            score += 5
        if re.search(r"^\s*(?:for|if|while|return)\b", code) and len(code.splitlines()) < 8:
            score -= 100
        return score

    @classmethod
    def extract_code(cls, text: str) -> tuple[str | None, str]:
        if not text or not text.strip():
            return None, ""
        fenced = re.findall(r"```([a-zA-Z0-9_+#.-]*)\s*\n(.*?)```", text, re.DOTALL)
        candidates: list[tuple[int, str, str]] = []
        for label, code in fenced:
            code = code.strip()
            lang = cls.normalize_language(label) if label.strip() else cls.detect_language(code)
            if lang in cls.SUPPORTED:
                candidates.append((cls._score(lang, code, bool(label.strip())), lang, code))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            _, lang, code = candidates[0]
            return lang, code
        code = text.strip()
        detected = cls.detect_language(code)
        if detected in cls.SUPPORTED and cls._is_complete(detected, code):
            return detected, code
        return None, ""

    @classmethod
    def detect_language(cls, code: str) -> str:
        text = code.strip()
        if re.search(r"#\s*include\s*[<\"]", text) and (
            "using namespace std" in text or "std::" in text or re.search(r"#\s*include\s*[<\"](?:iostream|vector|string|algorithm)", text)
        ):
            return "cpp"
        if re.search(r"#\s*include\s*[<\"](?:stdio\.h|stdlib\.h|string\.h)", text):
            return "c"
        if re.search(r"\b(?:public\s+)?static\s+void\s+main\s*\(", text):
            return "java"
        if re.search(r"\bpackage\s+main\b|\bfunc\s+main\s*\(", text):
            return "go"
        if re.search(r"\bfn\s+main\s*\(|println!\s*\(", text):
            return "rust"
        if re.search(r"\b(?:interface|type)\s+\w+", text) and ":" in text:
            return "typescript"
        if re.search(r"\b(?:console\.log|const|let|var|function|require\s*\()\b", text):
            return "javascript"
        if re.search(r"(^|\n)\s*(?:import |from |def |class |print\s*\(|if __name__)", text):
            return "python"
        return "unknown"

    @staticmethod
    def extract_test_cases(prompt: str) -> tuple[str | None, str | None]:
        if not prompt:
            return None, None
        p = prompt.strip()
        both_patterns = [
            r"For input\s+(.+?),\s*(?:the\s+)?expected output\s+(?:is|should be)\s+(.+?)(?:\.|$)",
            r"Input:\s*([^\n]+?)\s*\n\s*Output:\s*([^\n]+?)(?:\n|$)",
            r"Input:\s*([^,\n]+?),\s*Output:\s*(.+?)(?:\.|$)",
            r"Input\s*=\s*(.+?),\s*(?:expected\s+)?Output\s*=\s*(.+?)(?:\.|$)",
        ]
        for pattern in both_patterns:
            match = re.search(pattern, p, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip(), match.group(2).strip()

        input_only = [
            r"Input:\s*([^\n.;]+)",
            r"For input\s+([^,.;\n]+)(?:[,.;]|$)",
        ]
        for pattern in input_only:
            match = re.search(pattern, p, re.IGNORECASE)
            if match:
                value = match.group(1).strip()
                if value:
                    return value, None

        expected_only = [
            r"expected output\s+(?:is|should be)\s+(.+?)(?:\.|$)",
            r"Output:\s*(.+?)(?:\n|$)",
        ]
        for pattern in expected_only:
            match = re.search(pattern, p, re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()
                if value:
                    return None, value
        return None, None

    @staticmethod
    def compare_output(actual: str, expected: str | None) -> ComparisonState:
        if expected is None:
            return ComparisonState.UNAVAILABLE
        return ComparisonState.MATCH if (actual or "").strip() == expected.strip() else ComparisonState.MISMATCH

    def _toolchain_available(self, language: str) -> bool:
        tools = {
            "c": "gcc", "cpp": "g++", "java": "javac", "javascript": "node",
            "typescript": "tsc", "go": "go", "rust": "rustc",
        }
        return language == "python" or bool(shutil.which(tools.get(language, "")))

    @staticmethod
    def _safe_env() -> dict[str, str]:
        allowed = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP", "LANG", "LC_ALL"}
        return {k: v for k, v in os.environ.items() if k in allowed}

    def _run_subprocess(self, command: list[str], test_input: str | None, cwd: str | None = None) -> tuple[bool, str, str]:
        try:
            result = subprocess.run(
                command,
                input=test_input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                cwd=cwd,
                env=self._safe_env(),
            )
            return result.returncode == 0, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            return False, "", f"Timed out after {self.timeout}s."
        except FileNotFoundError as exc:
            return False, "", f"TOOLCHAIN_UNAVAILABLE: {exc}"
        except Exception as exc:
            return False, "", str(exc)

    def _run_compiled(self, code: str, compiler: str, source_name: str, flags: list[str], test_input: str | None) -> tuple[bool, str, str]:
        if not shutil.which(compiler):
            return False, "", f"TOOLCHAIN_UNAVAILABLE: {compiler} is not installed or is not available on PATH."
        with tempfile.TemporaryDirectory(prefix="vyzer-verify-") as td:
            source = os.path.join(td, source_name)
            exe = os.path.join(td, "main.exe" if os.name == "nt" else "main")
            try:
                with open(source, "w", encoding="utf-8", newline="") as fh:
                    fh.write(code)
                result = subprocess.run(
                    flags + [source, "-o", exe], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout, cwd=td, env=self._safe_env()
                )
                if result.returncode != 0:
                    return False, result.stdout, result.stderr
                if test_input is None:
                    return True, "", ""
                return self._run_subprocess([exe], test_input, cwd=td)
            except subprocess.TimeoutExpired:
                return False, "", f"Timed out after {self.timeout}s."
            except FileNotFoundError as exc:
                return False, "", f"TOOLCHAIN_UNAVAILABLE: {exc}"
            except Exception as exc:
                return False, "", str(exc)

    def _run_java(self, code: str, test_input: str | None) -> tuple[bool, str, str]:
        if not shutil.which("javac") or not shutil.which("java"):
            return False, "", "TOOLCHAIN_UNAVAILABLE: Java toolchain is not installed or is not available on PATH."
        with tempfile.TemporaryDirectory(prefix="vyzer-verify-") as td:
            source = os.path.join(td, "Main.java")
            try:
                with open(source, "w", encoding="utf-8", newline="") as fh:
                    fh.write(code)
                result = subprocess.run(["javac", source], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout, cwd=td, env=self._safe_env())
                if result.returncode != 0:
                    return False, result.stdout, result.stderr
                if test_input is None:
                    return True, "", ""
                return self._run_subprocess(["java", "Main"], test_input, cwd=td)
            except subprocess.TimeoutExpired:
                return False, "", f"Timed out after {self.timeout}s."
            except FileNotFoundError as exc:
                return False, "", f"TOOLCHAIN_UNAVAILABLE: {exc}"
            except Exception as exc:
                return False, "", str(exc)

    def _run_typescript(self, code: str, test_input: str | None) -> tuple[bool, str, str]:
        if not shutil.which("tsc"):
            return False, "", "TOOLCHAIN_UNAVAILABLE: tsc is not installed or is not available on PATH."
        with tempfile.TemporaryDirectory(prefix="vyzer-verify-") as td:
            source = os.path.join(td, "main.ts")
            try:
                with open(source, "w", encoding="utf-8", newline="") as fh:
                    fh.write(code)
                result = subprocess.run(["tsc", source, "--target", "ES2020", "--module", "commonjs", "--outDir", td], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout, cwd=td, env=self._safe_env())
                if result.returncode != 0:
                    return False, result.stdout, result.stderr
                if test_input is None:
                    return True, "", ""
                return self._run_subprocess(["node", os.path.join(td, "main.js")], test_input, cwd=td)
            except subprocess.TimeoutExpired:
                return False, "", f"Timed out after {self.timeout}s."
            except FileNotFoundError as exc:
                return False, "", f"TOOLCHAIN_UNAVAILABLE: {exc}"
            except Exception as exc:
                return False, "", str(exc)

    def _run_go(self, code: str, test_input: str | None) -> tuple[bool, str, str]:
        if not shutil.which("go"):
            return False, "", "TOOLCHAIN_UNAVAILABLE: go is not installed or is not available on PATH."
        with tempfile.TemporaryDirectory(prefix="vyzer-verify-") as td:
            source = os.path.join(td, "main.go")
            try:
                with open(source, "w", encoding="utf-8", newline="") as fh:
                    fh.write(code)
                result = subprocess.run(["go", "build", "-o", os.path.join(td, "main"), source], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout, cwd=td, env=self._safe_env())
                if result.returncode != 0:
                    return False, result.stdout, result.stderr
                if test_input is None:
                    return True, "", ""
                exe = os.path.join(td, "main.exe" if os.name == "nt" else "main")
                return self._run_subprocess([exe], test_input, cwd=td)
            except subprocess.TimeoutExpired:
                return False, "", f"Timed out after {self.timeout}s."
            except FileNotFoundError as exc:
                return False, "", f"TOOLCHAIN_UNAVAILABLE: {exc}"
            except Exception as exc:
                return False, "", str(exc)

    def run(self, language: str, code: str, test_input: str | None = None) -> tuple[bool, str, str]:
        language = self.normalize_language(language)
        if language == "python":
            if test_input is None:
                try:
                    compile(code, "<vyzer-python>", "exec")
                    return True, "", ""
                except SyntaxError as exc:
                    return False, "", str(exc)
            return self._run_subprocess([sys.executable, "-c", code], test_input)
        if language == "javascript":
            if test_input is None:
                return self._run_javascript_check(code)
            return self._run_subprocess(["node", "-e", code], test_input)
        if language == "typescript":
            return self._run_typescript(code, test_input)
        if language == "c":
            return self._run_compiled(code, "gcc", "main.c", ["gcc", "-std=c11"], test_input)
        if language == "cpp":
            return self._run_compiled(code, "g++", "main.cpp", ["g++", "-std=c++17"], test_input)
        if language == "java":
            return self._run_java(code, test_input)
        if language == "go":
            return self._run_go(code, test_input)
        if language == "rust":
            return self._run_compiled(code, "rustc", "main.rs", ["rustc"], test_input)
        return False, "", f"Unsupported language: {language}"

    def _run_javascript_check(self, code: str) -> tuple[bool, str, str]:
        if not shutil.which("node"):
            return False, "", "TOOLCHAIN_UNAVAILABLE: node is not installed or is not available on PATH."
        with tempfile.TemporaryDirectory(prefix="vyzer-verify-") as td:
            path = os.path.join(td, "main.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(code)
            return self._run_subprocess(["node", "--check", path], None, cwd=td)

    @staticmethod
    def _environment_failure(stderr: str) -> bool:
        return "toolchain_unavailable:" in (stderr or "").lower()

    def _evaluate(self, language: str, code: str, test_input: str | None, expected_output: str | None) -> tuple[VerificationState, str, str, ComparisonState]:
        if language in self.SUPPORTED and not self._toolchain_available(language):
            return VerificationState.ENVIRONMENT_UNAVAILABLE, "", f"TOOLCHAIN_UNAVAILABLE: {language} toolchain is unavailable.", ComparisonState.UNAVAILABLE

        # No concrete input means there is no permissible runtime correctness claim.
        if test_input is None:
            ok, stdout, stderr = self.run(language, code, None)
            if not ok:
                if self._environment_failure(stderr):
                    return VerificationState.ENVIRONMENT_UNAVAILABLE, stdout, stderr, ComparisonState.UNAVAILABLE
                return VerificationState.FAILED, stdout, stderr, ComparisonState.UNAVAILABLE
            return VerificationState.COMPILED, stdout, stderr, ComparisonState.UNAVAILABLE

        ok, stdout, stderr = self.run(language, code, test_input)
        if not ok:
            if self._environment_failure(stderr):
                return VerificationState.ENVIRONMENT_UNAVAILABLE, stdout, stderr, ComparisonState.UNAVAILABLE
            return VerificationState.FAILED, stdout, stderr, ComparisonState.UNAVAILABLE

        if expected_output is None:
            return VerificationState.EXECUTED, stdout, stderr, ComparisonState.UNAVAILABLE

        comparison = self.compare_output(stdout, expected_output)
        if comparison is ComparisonState.MATCH:
            return VerificationState.VERIFIED, stdout, stderr, comparison
        return VerificationState.EXECUTED, stdout, stderr, comparison

    def verify(self, model: str, api_messages: list[dict[str, Any]], reply: str, temperature: float = 0.2, max_tokens: int = 4096, num_ctx: int = 4096, test_input: str | None = None, expected_output: str | None = None) -> VerificationResult:
        if not reply or not reply.strip():
            return VerificationResult(reply or "", VerificationState.GENERATED, note="No response to verify.")
        original_reply = reply
        language, code = self.extract_code(reply)
        if not code:
            return VerificationResult(original_reply, VerificationState.NOT_APPLICABLE, note="No executable code candidate detected.")
        if language == "unknown":
            language = self.detect_language(code)
        if language not in self.SUPPORTED or not self._is_complete(language, code):
            return VerificationResult(original_reply, VerificationState.NOT_APPLICABLE, language=language, note="No complete executable code candidate detected safely.")

        original_test_input = test_input
        original_expected_output = expected_output
        current_reply, current_code, current_language = original_reply, code, language
        last_stdout = last_stderr = ""
        last_comparison = ComparisonState.UNAVAILABLE

        for attempt in range(1, self.max_attempts + 1):
            state, stdout, stderr, comparison = self._evaluate(current_language, current_code, original_test_input, original_expected_output)
            last_stdout, last_stderr, last_comparison = stdout, stderr, comparison
            logger.info("verification attempt=%s language=%s input=%s expected=%s state=%s comparison=%s", attempt, current_language, original_test_input is not None, original_expected_output is not None, state.value, comparison.value)

            if state is VerificationState.VERIFIED:
                return VerificationResult(current_reply, state, current_language, f"[TEST] {current_language.upper()} verified successfully (runtime output matched).", stdout, stderr, comparison, current_reply != original_reply, attempt, True, True)
            if state is VerificationState.COMPILED:
                note = "[TEST] Code compiled successfully; no concrete test input was available, so runtime correctness was not established."
                return VerificationResult(current_reply, state, current_language, note, stdout, stderr, comparison, current_reply != original_reply, attempt, False, original_expected_output is not None)
            if state is VerificationState.EXECUTED and original_expected_output is None:
                return VerificationResult(current_reply, state, current_language, f"[TEST] {current_language.upper()} executed successfully; output was not verified because no expected output was supplied.", stdout, stderr, comparison, current_reply != original_reply, attempt, True, False)
            if state is VerificationState.ENVIRONMENT_UNAVAILABLE:
                return VerificationResult(original_reply, state, current_language, f"[WARN] {current_language.upper()} toolchain is unavailable; verification was not established.", stdout, stderr, comparison, False, attempt, original_test_input is not None, original_expected_output is not None)

            if attempt >= self.max_attempts:
                break

            fix_prompt = self._repair_prompt(current_language, current_code, stdout, stderr, original_test_input, original_expected_output)
            try:
                fixed_reply = self.llm.chat(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are Vyzer's programming correction assistant. Return one complete corrected program and never redefine the user's test case."},
                        {"role": "user", "content": "ORIGINAL USER CONVERSATION:\n\n" + str(api_messages) + "\n\n" + fix_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=max_tokens,
                    num_ctx=num_ctx,
                )
            except Exception as exc:
                return VerificationResult(original_reply, VerificationState.FAILED, language, f"[WARN] Automatic code repair was unavailable. The original answer is preserved. ({exc})", last_stdout, last_stderr, last_comparison, False, attempt, original_test_input is not None, original_expected_output is not None)
            if not fixed_reply or not fixed_reply.strip():
                return VerificationResult(original_reply, VerificationState.FAILED, language, "[WARN] Automatic repair returned no usable answer. The original answer is preserved.", last_stdout, last_stderr, last_comparison, False, attempt, original_test_input is not None, original_expected_output is not None)
            new_language, fixed_code = self.extract_code(fixed_reply.strip())
            if not fixed_code:
                continue
            if new_language == "unknown":
                new_language = self.detect_language(fixed_code)
            if new_language not in self.SUPPORTED or not self._is_complete(new_language, fixed_code):
                continue
            current_reply, current_code, current_language = fixed_reply.strip(), fixed_code, new_language

        short = (last_stderr or last_stdout or "").strip()
        if len(short) > 700:
            short = short[-700:]
        note = "[WARN] Verification failed after automatic repair attempts. The original answer is preserved."
        if last_comparison is ComparisonState.MISMATCH and original_expected_output is not None:
            note = "[WARN] Verification failed: runtime output did not match the original expected output. The original answer is preserved."
        if short:
            note += "\n\nVerifier diagnostics:\n\n" + short
        return VerificationResult(original_reply, VerificationState.FAILED, language, note, last_stdout, last_stderr, last_comparison, False, self.max_attempts, original_test_input is not None, original_expected_output is not None)

    @staticmethod
    def _repair_prompt(language: str, code: str, stdout: str, stderr: str, test_input: str | None, expected_output: str | None) -> str:
        prompt = f"""Repair this {language} program.\n\nPROGRAM:\n```{language}\n{code}\n```\n\nORIGINAL TEST INPUT (IMMUTABLE):\n{test_input if test_input is not None else '[none supplied]'}\n\nSTDOUT:\n{stdout}\n\nSTDERR:\n{stderr}\n"""
        if expected_output is not None:
            prompt += f"""\nORIGINAL EXPECTED OUTPUT (IMMUTABLE):\n{expected_output}\n\nCRITICAL REQUIREMENTS:\n- The original expected output is authoritative and MUST NOT be changed.\n- The original test input is authoritative and MUST NOT be changed.\n- The repaired program must produce exactly the original expected output for the original input.\n- Do not print interactive prompts or explanatory labels unless the expected output explicitly requires them.\n- Return one complete corrected program only.\n"""
        prompt += "\nDo not return a fragment, prose-only answer, or a claim of success."
        return prompt
