"""
Smart conversation memory for Vyzer.

Features
--------
✓ Estimates prompt size
✓ Automatically summarizes old conversations
✓ Keeps recent messages untouched
✓ Prevents context overflow
✓ Works with Ollama/OpenAI compatible clients
"""

from typing import List, Dict

SUMMARY_PROMPT = """
You are creating memory for an AI assistant.

Summarize the conversation while preserving:

- Important facts
- User preferences
- Completed tasks
- Unfinished tasks
- Code that may still be relevant
- File names
- Errors that still matter

DO NOT include greetings or filler.

Write concise bullet points.
"""
class ConversationMemory:

    def __init__(
        self,
        client,
        model: str,
        max_context: int = 4096,
        reserve_tokens: int = 1200,
    ):
        self.client = client
        self.model = model
        self.max_context = max_context
        self.reserve_tokens = reserve_tokens

        self.summary = ""

    def estimate_tokens(
        self,
        messages: List[Dict],
    ) -> int:

        chars = 0

        for m in messages:

            content = m.get("content", "")

            if isinstance(content, list):

                for item in content:

                    if item.get("type") == "text":
                        chars += len(
                            item.get("text", "")
                        )

            else:
                chars += len(str(content))

        return chars // 4

    def should_summarize(
        self,
        messages: List[Dict],
    ) -> bool:

        tokens = self.estimate_tokens(messages)

        return tokens > (
            self.max_context - self.reserve_tokens
        )

    def summarize(
        self,
        messages: List[Dict],
    ) -> str:

        conversation = []

        for m in messages:

            role = m["role"]
            content = m["content"]

            if isinstance(content, list):

                text = ""

                for part in content:

                    if part.get("type") == "text":
                        text += part.get("text", "")

                content = text

            conversation.append(
                f"{role.upper()}:\n{content}"
            )

        conversation = "\n\n".join(
            conversation
        )

        response = self.client.chat.completions.create(

            model=self.model,

            messages=[
                {
                    "role": "system",
                    "content": SUMMARY_PROMPT,
                },
                {
                    "role": "user",
                    "content": conversation,
                },
            ],

            temperature=0.2,
            stream=False,
        )

        self.summary = (
            response.choices[0]
            .message
            .content
        )

        return self.summary

    def build_messages(
        self,
        system_prompt: str,
        history: List[Dict],
    ) -> List[Dict]:

        if self.should_summarize(history):

            old_messages = history[:-6]
            recent_messages = history[-6:]

            if old_messages:
                self.summarize(old_messages)

            history = recent_messages

        api_messages = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        if self.summary:

            api_messages.append(
                {
                    "role": "system",
                    "content":
                        "Conversation summary:\n\n"
                        + self.summary,
                }
            )

        api_messages.extend(history)

        return api_messages
