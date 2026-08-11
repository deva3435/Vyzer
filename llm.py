"""
Handles all communication with Ollama.

Nothing in Streamlit should directly call
client.chat.completions.create() anymore.

Everything goes through this class.
"""

import os

from openai import OpenAI


class LLMClient:

    def __init__(
        self,
        base_url=None,
        api_key="ollama",
        timeout=None,
    ):

        configured_base = base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/v1"
        configured_timeout = timeout if timeout is not None else float(os.getenv("MODEL_TIMEOUT_SECONDS", "600"))
        self.client = OpenAI(
            base_url=configured_base,
            api_key=os.getenv("OLLAMA_API_KEY", api_key),
            timeout=configured_timeout,
        )

    # -------------------------------------------------------------

    def stream_chat(
        self,
        model,
        messages,
        temperature=0.4,
        max_tokens=1024,
        num_ctx=4096,
    ):

        stream = self.client.chat.completions.create(

            model=model,

            messages=messages,

            temperature=temperature,

            max_tokens=max_tokens,

            stream=True,

            extra_body={
                "options": {
                    "num_ctx": num_ctx
                }
            },
        )

        full_reply = ""

        for chunk in stream:

            if (
                chunk.choices
                and chunk.choices[0].delta.content
            ):

                text = chunk.choices[0].delta.content

                full_reply += text

                yield text, full_reply

    # -------------------------------------------------------------

    def chat(
        self,
        model,
        messages,
        temperature=0.4,
        max_tokens=1024,
        num_ctx=4096,
    ):

        response = self.client.chat.completions.create(

            model=model,

            messages=messages,

            temperature=temperature,

            max_tokens=max_tokens,

            stream=False,

            extra_body={
                "options": {
                    "num_ctx": num_ctx
                }
            },
        )

        return response.choices[0].message.content

    # -------------------------------------------------------------

    def continue_chat(
        self,
        model,
        messages,
        previous_reply,
        temperature=0.4,
        max_tokens=1024,
        num_ctx=4096,
    ):
        """
        Continue an answer that appears to have been truncated.

        The model is explicitly told not to repeat the previous
        answer and to continue from where it stopped.
        """

        continuation_messages = messages + [
            {
                "role": "assistant",
                "content": previous_reply,
            },
            {
                "role": "user",
                "content": (
                    "Your previous answer was incomplete and "
                    "stopped before finishing the response.\n\n"
                    "Continue from exactly where you stopped.\n"
                    "Do NOT repeat anything already written.\n"
                    "Complete the remaining parts of the user's "
                    "question.\n"
                    "Return only the continuation."
                ),
            },
        ]

        return self.chat(
            model=model,
            messages=continuation_messages,
            temperature=temperature,
            max_tokens=max_tokens,
            num_ctx=num_ctx,
        )

    # -------------------------------------------------------------

    def safe_stream_chat(
        self,
        model,
        messages,
        temperature=0.7,
        max_tokens=1024,
    ):
        """
        Automatically retries with less context
        if Ollama complains.
        """

        try:

            yield from self.stream_chat(
                model,
                messages,
                temperature,
                max_tokens,
            )

        except Exception as e:

            error = str(e).lower()

            if (
                "context" in error
                or "4096" in error
                or "8192" in error
            ):

                trimmed = [
                    messages[0],
                    *messages[-6:]
                ]

                yield from self.stream_chat(
                    model,
                    trimmed,
                    temperature,
                    max_tokens,
                )

            else:
                raise
