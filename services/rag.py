"""
Handles:

- Tavily search
- PDF reading
- TXT reading
- Document attachment

Nothing in app.py should directly touch
Tavily or PdfReader anymore.
"""

import json
import urllib.request
import urllib.error

from pypdf import PdfReader


class RAGService:

    def __init__(
        self,
        tavily_api_key="",
        max_document_chars=15000,
    ):

        self.api_key = tavily_api_key
        self.max_document_chars = max_document_chars

    # ----------------------------------------------------

    def search(
        self,
        query,
        max_results=5,
    ):

        if not self.api_key:

            return "", [], "No Tavily API key configured."

        payload = json.dumps({

            "api_key": self.api_key,

            "query": query,

            "search_depth": "basic",

            "max_results": max_results,

            "include_answer": False,

        }).encode("utf-8")

        req = urllib.request.Request(

            "https://api.tavily.com/search",

            data=payload,

            headers={
                "Content-Type":
                "application/json"
            },

            method="POST",
        )

        try:

            with urllib.request.urlopen(
                req,
                timeout=15,
            ) as r:

                data = json.loads(
                    r.read().decode("utf-8")
                )

        except urllib.error.HTTPError as e:

            detail = ""

            try:

                detail = json.loads(
                    e.read().decode("utf-8")
                ).get(
                    "detail",
                    {}
                ).get(
                    "error",
                    "",
                )

            except Exception:
                pass

            return "", [], f"HTTP {e.code}: {detail}"

        except Exception as e:

            return "", [], str(e)

        results = data.get(
            "results",
            [],
        )

        if not results:

            return "", [], "No results."

        snippets = []

        sources = []

        for r in results:

            title = r.get(
                "title",
                "Untitled",
            )

            url = r.get(
                "url",
                "",
            )

            content = r.get(
                "content",
                "",
            )

            snippets.append(

                f"Source: {title}\n"
                f"URL: {url}\n"
                f"{content}"

            )

            sources.append({

                "title": title,

                "url": url,

            })

        return (

            "\n\n".join(snippets),

            sources,

            "",

        )

    # ----------------------------------------------------

    def extract_document(
        self,
        uploaded_file,
    ):

        text = ""

        try:

            if uploaded_file.name.endswith(".pdf"):

                reader = PdfReader(
                    uploaded_file
                )

                for page in reader.pages:

                    text += (
                        page.extract_text()
                        or ""
                    )

                if not text.strip():

                    return "", (
                        "No extractable text "
                        "found in PDF."
                    )

            elif uploaded_file.name.endswith(".txt"):

                text = uploaded_file.read().decode(
                    "utf-8"
                )

            else:

                return "", (
                    "Unsupported file type."
                )

        except Exception as e:

            return "", str(e)

        if len(text) > self.max_document_chars:

            text = (
                text[
                    : self.max_document_chars
                ]
                + "\n\n"
                + "[Document truncated]"
            )

        return text, ""

    # ----------------------------------------------------

    def build_system_prompt(

        self,

        base_prompt,

        web_context="",

        doc_name="",

        doc_text="",

    ):

        prompt = base_prompt

        if web_context:

            prompt += (

                "\n\n"

                "[LIVE WEB SEARCH RESULTS]\n\n"

                + web_context

            )

        if doc_text:

            prompt += (

                "\n\n"

                f"[ATTACHED DOCUMENT: {doc_name}]\n\n"

                + doc_text

            )

        return prompt
