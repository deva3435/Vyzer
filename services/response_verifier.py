"""
Accuracy-focused second-pass verification for Vyzer.

The verifier:
1. Decides whether an answer needs verification.
2. Uses an independent verification model.
3. Checks logical, factual, mathematical, scientific,
   historical, technical, and instruction-following errors.
4. Only requests a correction when an actual problem is found.

If verification fails, the original answer is preserved.
"""

# -------------------------------------------------------------------------
# VERIFICATION PROMPT
# -------------------------------------------------------------------------

VERDICT_PROMPT = """
You are Vyzer's strict answer-quality verifier.

You are reviewing an answer produced by another AI model.

Your job is NOT to rewrite the answer.

Determine whether the proposed answer is reliable enough
to show to the user.

Check ALL of the following that apply:

1. FACTUAL ACCURACY
- Are factual claims correct?
- Are definitions technically correct?
- Are historical events, dates, people, and terminology correct?
- Are scientific claims consistent with established knowledge?
- Are technical/programming claims correct?

2. LOGICAL CORRECTNESS
- Does the conclusion actually follow from the reasoning?
- Are there contradictions?
- Are assumptions unsupported?
- Does every step follow from the previous step?

3. MATHEMATICS
- Check every calculation.
- Check arithmetic.
- Check units.
- Check percentages, fractions, equations, and probability.
- Make sure the final answer follows from the calculations.

4. TECHNICAL / PROGRAMMING
- Check whether the proposed solution actually works.
- Check stated time and space complexity.
- Check that constraints from the question are respected.
- Check for edge cases when they matter.

5. SCIENCE
- Check scientific mechanisms and terminology.
- Watch for plausible-sounding but incorrect explanations.
- Check whether cause-and-effect claims are justified.

6. INSTRUCTIONS
- Check that every explicit part of the user's request was answered.
- Check requested quantities, constraints, exclusions,
  formats, and conditions.

7. INTERNAL CONSISTENCY
- The answer must not contradict itself.
- Definitions must remain consistent.
- Examples must agree with the explanation.
- The final conclusion must agree with the reasoning.

IMPORTANT:

Do NOT mark an answer incorrect merely because you would
phrase it differently.

Do NOT penalize harmless omissions that do not affect correctness.

Do NOT invent an error.

If you identify a problem, give the specific problem briefly.

If you are genuinely unable to determine whether an important
claim is correct, treat that as a verification failure rather
than assuming the answer is correct.

Return EXACTLY one of:

CORRECT

INCORRECT: <specific error>

UNCERTAIN: <specific claim that could not be reliably verified>

Return nothing else.
"""


# -------------------------------------------------------------------------
# CORRECTION PROMPT
# -------------------------------------------------------------------------

CORRECTION_PROMPT = """
You are the final answer writer for Vyzer.

The original assistant answer was found to contain an error.

Return the COMPLETE corrected answer to the user's original question.

Requirements:

- Fix the specific error identified by the verifier.
- Re-check the entire answer, not just the identified sentence.
- Make sure no new contradictions are introduced.
- Preserve correct information from the original answer.
- Answer every part of the original question.
- Respect every explicit constraint from the user.
- Do not mention the verifier.
- Do not mention this correction process.
- Do not apologize.
- Do not say that the previous answer was wrong.
- Return ONLY the corrected answer.
"""


class ResponseVerifier:

    @staticmethod
    def is_status_message(text):
        cleaned = (text or "").strip()

        if not cleaned:
            return True

        lowered = cleaned.lower()

        prefixes = (
            "answer checked",
            "answer corrected",
            "code verified",
            "could not fully verify",
            "checked and corrected",
            "corrected answer",
            "verified successfully",
            "status:",
            "verification:",
        )

        if lowered in {
            "correct",
            "incorrect",
            "uncertain",
            "verified",
            "not verified",
            "passed",
            "failed",
        }:
            return True

        if lowered.startswith(prefixes):
            return True

        return False

    def __init__(
        self,
        llm,
        verification_model=None,
    ):
        self.llm = llm
        self.verification_model = verification_model

    # -----------------------------------------------------------------
    # Decide whether verification is worthwhile
    # -----------------------------------------------------------------

    @staticmethod
    def should_verify(
        prompt,
        response,
    ):

        prompt = (prompt or "").lower()
        response = (response or "").lower()

        text = prompt + " " + response

        # -------------------------------------------------------------
        # High-value verification triggers
        # -------------------------------------------------------------

        markers = [

            # Mathematics
            "calculate",
            "calculation",
            "equation",
            "percentage",
            "probability",
            "fraction",
            "ratio",
            "math",
            "mathematical",

            # Reasoning
            "prove",
            "derive",
            "reason",
            "reasoning",
            "step by step",
            "solve",
            "logic",
            "puzzle",
            "riddle",
            "deduce",
            "deduction",

            # Science
            "chemistry",
            "chemical",
            "biology",
            "biological",
            "physics",
            "physical",
            "reaction",
            "molecule",
            "atom",
            "photosynthesis",
            "genetics",

            # Academic subjects
            "history",
            "historical",
            "archaeology",
            "archaeological",
            "psychology",
            "psychological",
            "economics",
            "literature",
            "grammar",

            # Technical
            "algorithm",
            "complexity",
            "o(n)",
            "o(1)",
            "code",
            "coding",
            "program",
            "python",
            "javascript",
            "java",
            "sql",
            "debug",

            # Causal / analytical
            "cause",
            "causes",
            "causation",
            "correlation",
            "confounding",
            "compare",
            "comparison",
            "evaluate",
            "tradeoff",
            "trade-off",

            # Explicit accuracy requests
            "accurate",
            "accuracy",
            "correct",
            "verify",
            "fact",
            "facts",
            "is this true",
            "is this correct",
        ]

        if any(
            marker in text
            for marker in markers
        ):
            return True

        # -------------------------------------------------------------
        # Long substantive answers deserve a check even if the
        # question doesn't contain an obvious keyword.
        # -------------------------------------------------------------

        if len(response.strip()) >= 900:
            return True

        return False

    # -----------------------------------------------------------------
    # Determine verification model
    # -----------------------------------------------------------------

    def get_verification_model(
        self,
        fallback_model,
    ):

        if self.verification_model:
            return self.verification_model

        return fallback_model

    # -----------------------------------------------------------------
    # Main verification
    # -----------------------------------------------------------------

    def verify(
        self,
        model,
        prompt,
        response,
        temperature=0.1,
    ):

        if not response or not response.strip():
            return response, ""

        if not self.should_verify(
            prompt,
            response,
        ):
            return response, ""

        verification_model = self.get_verification_model(
            model
        )

        # -------------------------------------------------------------
        # Stage 1: independent verdict
        # -------------------------------------------------------------

        verdict_messages = [

            {
                "role": "system",
                "content": VERDICT_PROMPT,
            },

            {
                "role": "user",
                "content": (
                    "USER QUESTION:\n\n"
                    + prompt
                    + "\n\n"
                    "PROPOSED ANSWER:\n\n"
                    + response
                ),
            },
        ]

        try:

            verdict = self.llm.chat(
                model=verification_model,
                messages=verdict_messages,
                temperature=0.0,
                max_tokens=256,
                num_ctx=4096,
            )

        except Exception:

            # Verification must never destroy a valid answer.
            return response, ""

        if not verdict:
            return response, ""

        verdict = verdict.strip()

        if not verdict:
            return response, ""

        verdict_upper = verdict.upper()

        # -------------------------------------------------------------
        # Correct
        # -------------------------------------------------------------

        if verdict_upper.startswith("CORRECT"):

            return (
                response,
                "Answer checked.",
            )

        # -------------------------------------------------------------
        # Uncertain
        # -------------------------------------------------------------

        if verdict_upper.startswith("UNCERTAIN"):

            return (
                response,
                "Answer could not be fully verified.",
            )

        # -------------------------------------------------------------
        # Unexpected verifier response
        # -------------------------------------------------------------

        if not verdict_upper.startswith("INCORRECT"):

            return response, ""

        # -------------------------------------------------------------
        # Stage 2: correction
        # -------------------------------------------------------------

        correction_messages = [

            {
                "role": "system",
                "content": CORRECTION_PROMPT,
            },

            {
                "role": "user",
                "content": (
                    "USER QUESTION:\n\n"
                    + prompt
                    + "\n\n"
                    "ORIGINAL ANSWER:\n\n"
                    + response
                    + "\n\n"
                    "VERIFIER'S FINDING:\n\n"
                    + verdict
                ),
            },
        ]

        try:

            corrected = self.llm.chat(
                model=verification_model,
                messages=correction_messages,
                temperature=0.1,
                max_tokens=4096,
                num_ctx=8192,
            )

        except Exception:

            return response, ""

        if not corrected:
            return response, ""

        corrected = corrected.strip()

        if not corrected:
            return response, ""

        return (
            corrected,
            "Answer checked and corrected.",
        )
