"""
Detects obvious signs that an LLM response was cut off
before it finished.
"""

import re


class ResponseCompletion:

    @staticmethod
    def looks_incomplete(response):

        if not response:
            return True

        text = response.strip()

        if not text:
            return True

        # ---------------------------------------------------------
        # Obvious unfinished markdown/code structures
        # ---------------------------------------------------------

        if text.count("```") % 2 != 0:
            return True

        # Unfinished markdown table row
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        if lines:

            last = lines[-1]

            # A table row ending with a pipe but containing
            # very little content is often truncated.
            if (
                last.startswith("|")
                and last.endswith("|")
            ):

                cells = [
                    cell.strip()
                    for cell in last.split("|")[1:-1]
                ]

                if len(cells) <= 2:
                    return True

                # If the last cell is obviously incomplete
                if cells[-1] == "":
                    return True

        # ---------------------------------------------------------
        # Ends with punctuation that strongly suggests continuation
        # ---------------------------------------------------------

        incomplete_endings = (
            "and",
            "or",
            "but",
            "because",
            "therefore",
            "such as",
            "including",
            "for example",
            "which",
            "that",
            "while",
            "after",
            "before",
            "when",
            "if",
            "to",
            "with",
            "by",
            "of",
            "the",
            "a",
            "an",
        )

        lower = text.lower()

        if lower.endswith(incomplete_endings):
            return True

        # ---------------------------------------------------------
        # Obvious unfinished numbered/list sections
        # ---------------------------------------------------------

        if re.search(
            r"(?:^|\n)\s*(?:\d+[\.\)]|[-*])\s*$",
            text,
        ):
            return True

        # ---------------------------------------------------------
        # Obvious unfinished heading
        # ---------------------------------------------------------

        if re.search(
            r"(?:^|\n)\s*#{1,6}\s+[^#\n]+$",
            text,
        ):

            # Only consider it incomplete when the heading
            # appears to be the final line.
            if lines and lines[-1].startswith("#"):
                return True

        return False
