"""
Vyzer v1.2

Main application entry point.

Modules

memory.py
llm.py
image_utils.py
model_router.py

Everything else remains here.
"""
import os
import json
import uuid
import time
import re
import subprocess
import urllib.request
import urllib.error
import base64
from datetime import datetime
from io import BytesIO

import streamlit as st
import pyperclip
import requests

from dotenv import load_dotenv
from platformdirs import user_data_dir

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle,
)
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
)

from llm import LLMClient
from memory import ConversationMemory
from image_utils import image_to_base64
import model_router as model_router_module
from services.rag import RAGService
from services.verifier import CodeVerifier, VerificationState, ComparisonState
from services.response_verifier import ResponseVerifier
from services.verification_pipeline import verify_generated_answer, should_run_response_verifier
from services.verification_ui import status_text
from services.model_display import actual_model_label
from ui.settings import (
    SettingsManager,
    settings_dialog,
)

ModelRouter = model_router_module.ModelRouter

# -----------------------------
# Environment
# -----------------------------

load_dotenv(override=True)

ENV_TAVILY_API_KEY = os.getenv(
    "TAVILY_API_KEY",
    "",
)

# -----------------------------
# Streamlit
# -----------------------------

st.set_page_config(

    page_title="Vyzer",

    page_icon=None,

    layout="wide",

    initial_sidebar_state="expanded",
)

# -----------------------------
# Directories
# -----------------------------

APP_NAME = "Vyzer"

DATA_DIR = user_data_dir(
    APP_NAME,
    appauthor=False,
)

CHAT_DIR = os.path.join(
    DATA_DIR,
    "chats",
)

SETTINGS_FILE = os.path.join(
    DATA_DIR,
    "settings.json",
)

os.makedirs(
    CHAT_DIR,
    exist_ok=True,
)

BACKEND_URL = os.getenv(
    "BACKEND_URL",
    "http://127.0.0.1:8000",
)


def backend_request(method, endpoint, token=None, payload=None):
    url = BACKEND_URL.rstrip("/") + endpoint
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            json=payload,
            timeout=20,
        )
    except Exception as exc:
        raise RuntimeError(f"Backend unavailable: {exc}") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text
        raise RuntimeError(detail)

    if not response.content:
        return {}

    try:
        return response.json()
    except Exception:
        return {"raw": response.text}


def auth_signup(email, password):
    result = backend_request(
        "POST",
        "/api/auth/signup",
        payload={"email": email, "password": password},
    )
    return result


def auth_login(email, password):
    result = backend_request(
        "POST",
        "/api/auth/login",
        payload={"email": email, "password": password},
    )
    return result


def auth_reset_password(email, password):
    result = backend_request(
        "POST",
        "/api/auth/reset-password",
        payload={"email": email, "new_password": password},
    )
    return result


# -----------------------------
# Settings
# -----------------------------

# -----------------------------
# Ollama
# -----------------------------

llm = LLMClient()

def check_ollama_running():

    try:

        ollama_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        req = urllib.request.Request(

            f"{ollama_base}/api/tags",

            method="GET",
        )

        with urllib.request.urlopen(
            req,
            timeout=3,
        ) as r:

            return r.status == 200

    except Exception:

        return False


def get_installed_models():

    try:

        models = llm.client.models.list()

        return [
            m.id
            for m in models.data
        ]

    except Exception:

        try:

            out = subprocess.check_output(
                ["ollama", "list"],
                text=True,
                encoding="utf-8",
                errors="replace",
                stderr=subprocess.STDOUT,
                timeout=5,
            )

            models = []

            for line in out.splitlines():

                if (
                    not line.strip()
                    or line.startswith("NAME")
                ):
                    continue

                models.append(
                    line.split()[0]
                )

            return models

        except Exception:

            return []


def extract_image_text(
    vision_model,
    image_b64,
    image_mime,
    user_instruction="",
):
    """
    Uses the vision model ONLY to extract the contents
    of an image. It does not solve the problem.
    """

    extraction_prompt = f"""
You are Vyzer's image extraction layer.

Read the image carefully.

Your job is ONLY to extract/transcribe the question
or information contained in the image.

DO NOT solve the problem.

DO NOT provide an answer.

DO NOT explain the solution.

Preserve exactly:

- programming language
- code
- variable names
- constraints
- examples
- input/output requirements
- mathematical notation
- every sub-question
- important formatting or conditions

If this is a programming problem, preserve the
complete programming problem and all requirements.

User instruction, if any:

{user_instruction}

Return ONLY the extracted question/content.
"""

    messages = [
        {
            "role": "system",
            "content": extraction_prompt,
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Extract the contents of this image.",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:{image_mime};base64,"
                            f"{image_b64}"
                        )
                    },
                },
            ],
        },
    ]

    return llm.chat(
        model=vision_model,
        messages=messages,
        temperature=0.1,
        max_tokens=4096,
        num_ctx=8192,
    )


available_models = get_installed_models()

router = ModelRouter(
    available_models
)
settings = SettingsManager(
    SETTINGS_FILE,
)

rag = RAGService(
    tavily_api_key=settings.get(
        "tavily_api_key",
        ENV_TAVILY_API_KEY,
    ),
)

verifier = CodeVerifier(llm)
verification_model = None

for model in available_models:
    if "deepseek-r1" in model.lower():
        verification_model = model
        break

response_verifier = ResponseVerifier(
    llm=llm,
    verification_model=verification_model,
)
# -----------------------------
# Memory
# -----------------------------

memory = ConversationMemory(

    llm.client,

    available_models[0] if available_models else "",
)

# -----------------------------
# Session State
# -----------------------------

DEFAULT_STATE = {

    "current_id": None,

    "messages": [],

    "attached_document": None,

    "pending_generation": False,

    "editing_message_idx": None,

    "renaming_chat_id": None,
    "last_used_model": None,
}

for key, value in DEFAULT_STATE.items():

    if key not in st.session_state:

        st.session_state[key] = value

if "auth_token" not in st.session_state:
    st.session_state.auth_token = None
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.auth_token:
    st.title("Vyzer")
    st.subheader("Sign in")

    auth_tab, signup_tab, reset_tab = st.tabs(["Login", "Sign up", "Reset password"])

    with auth_tab:
        email = st.text_input("Email", key="login_email")
        password = st.text_input("Password", type="password", key="login_password")
        if st.button("Login"):
            try:
                result = auth_login(email, password)
                # Clear any previous session state to prevent cross-user data leakage
                st.session_state.current_id = None
                st.session_state.messages = []
                st.session_state.attached_document = None
                st.session_state.pending_generation = False
                st.session_state.editing_message_idx = None
                st.session_state.renaming_chat_id = None
                st.session_state.last_used_model = None
                # Set new authentication
                st.session_state.auth_token = result["token"]
                st.session_state.auth_user = result["user"]
                st.success("Signed in.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with signup_tab:
        new_email = st.text_input("Email", key="signup_email")
        new_password = st.text_input("Password", type="password", key="signup_password")
        if st.button("Create account"):
            try:
                result = auth_signup(new_email, new_password)
                # Clear any previous session state to prevent cross-user data leakage
                st.session_state.current_id = None
                st.session_state.messages = []
                st.session_state.attached_document = None
                st.session_state.pending_generation = False
                st.session_state.editing_message_idx = None
                st.session_state.renaming_chat_id = None
                st.session_state.last_used_model = None
                # Set new authentication
                st.session_state.auth_token = result["token"]
                st.session_state.auth_user = result["user"]
                st.success("Account created.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with reset_tab:
        reset_email = st.text_input("Email", key="reset_email")
        reset_password_value = st.text_input("New password", type="password", key="reset_new_password")
        if st.button("Reset password"):
            try:
                result = auth_reset_password(reset_email, reset_password_value)
                # Clear any previous session state to prevent cross-user data leakage
                st.session_state.current_id = None
                st.session_state.messages = []
                st.session_state.attached_document = None
                st.session_state.pending_generation = False
                st.session_state.editing_message_idx = None
                st.session_state.renaming_chat_id = None
                st.session_state.last_used_model = None
                # Set new authentication
                st.session_state.auth_token = result["token"]
                st.session_state.auth_user = result["user"]
                st.success("Password reset successful.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.stop()

# -----------------------------
# Constants
# -----------------------------

PRESET_PROMPTS = {

    "General Assistant": """
You are Vyzer, a helpful, accurate, practical AI assistant.

Answer the user's actual question directly.

Priorities:
1. Correctness
2. Relevance
3. Practical usefulness
4. Clear reasoning
5. Concise presentation

Do not invent facts, ingredients, measurements, sources, or capabilities.
If you are uncertain about something important, say so instead of guessing.

Understand the user's intent before answering.
Before answering, verify that your response satisfies every explicit requirement in the user's request.

Pay special attention to:
- Requested quantities
- Number of items
- Constraints
- Required ingredients
- Requested programming language
- Requested format
- Requested output length
- Specific conditions or exclusions

Do not silently change, omit, or substitute an explicit requirement.

For example, if the user asks for a recipe using 3 bananas, the recipe must use 3 bananas. Do not give a recipe using 2 bananas unless you clearly explain why and adjust the recipe accordingly.

Before finalizing your answer, mentally check:
"Did I actually answer exactly what the user asked?"

Do not unnecessarily repeat the question.
Do not add irrelevant disclaimers or filler.
Do not add optional substitutions, alternatives, tips, or follow-up suggestions unless they are genuinely useful to the user's request.

When giving instructions:
- Give the required ingredients/materials first.
- Give numbered steps in the correct order.
- Include temperatures, quantities, timings, and important visual cues when relevant.
- Mention important substitutions only when useful.
- Account for common mistakes and explain how to avoid them.

When answering technical questions:
- Prefer correct, working solutions over overly clever ones.
- Explain important assumptions.
- If there are multiple valid approaches, recommend one and briefly explain why.

When the user asks for a recommendation or comparison:
- Identify the important criteria.
- Give a clear recommendation.
- Explain the tradeoffs.

Match the user's level of knowledge and requested detail.

Do not pretend to have performed an action, accessed information, or verified something unless you actually did.
""",

}
# -----------------------------------------------------------------------------
# Helper Functions & Tool Integrations
# -----------------------------------------------------------------------------

def title_from(text: str) -> str:
    t = " ".join(text.strip().split())
    return (t[:36] + "…") if len(t) > 36 else (t or "New chat")

def sanitize_message_for_storage(msg):
    clean_msg = {k: v for k, v in msg.items() if k != "image_bytes"}
    return clean_msg

def load_chats():
    token = st.session_state.get("auth_token")
    if token:
        # Authenticated users: ONLY load from backend API
        # No fallback to local storage to prevent cross-user data leakage
        try:
            result = backend_request("GET", "/api/conversations", token=token)
            conversations = result.get("conversations", [])
            normalized = []
            for item in conversations:
                # Handle timestamp parsing - backend may return ISO format strings or numbers
                updated_at = item.get("updated_at")
                if updated_at:
                    if isinstance(updated_at, str):
                        # Parse ISO format timestamp
                        try:
                            dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                            timestamp = int(dt.timestamp() * 1000)
                        except Exception:
                            timestamp = int(time.time() * 1000)
                    else:
                        # Already a number
                        timestamp = int(updated_at) if updated_at else int(time.time() * 1000)
                else:
                    timestamp = int(time.time() * 1000)

                normalized.append({
                    "id": item.get("id"),
                    "title": item.get("title") or "New chat",
                    "messages": item.get("messages", []),
                    "document": item.get("document"),
                    "updatedAt": timestamp,
                })
            return sorted(normalized, key=lambda x: x.get("updatedAt", 0), reverse=True)
        except Exception as exc:
            # If backend fails for authenticated user, return empty list
            # Do NOT fall back to local storage to prevent data leakage
            st.error(f"Failed to load conversations from backend: {exc}")
            return []

    # Non-authenticated (offline) mode: load from local storage only
    loaded = []
    for filename in os.listdir(CHAT_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(CHAT_DIR, filename)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    loaded.append(json.load(f))
            except Exception:
                pass
    return sorted(loaded, key=lambda x: x.get("updatedAt", 0), reverse=True)

def persist_chat(chat_id: str, messages: list, document=None):
    if not chat_id or not messages:
        return

    first_user = next((m for m in messages if m["role"] == "user"), None)
    title = title_from(first_user["content"] if first_user else "New chat")
    clean_messages = [sanitize_message_for_storage(m) for m in messages]

    token = st.session_state.get("auth_token")
    if token:
        try:
            synced_key = f"backend_synced_count_{chat_id}"
            previous_count = int(st.session_state.get(synced_key, 0))
            if previous_count == 0:
                try:
                    backend_request("GET", f"/api/conversations/{chat_id}", token=token)
                except Exception:
                    backend_request("POST", "/api/conversations", token=token, payload={"title": title})

            for msg in clean_messages[previous_count:]:
                payload = {
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                    "metadata": {k: v for k, v in msg.items() if k not in {"role", "content"}},
                }
                backend_request(
                    "POST",
                    f"/api/conversations/{chat_id}/messages",
                    token=token,
                    payload=payload,
                )
            st.session_state[synced_key] = len(clean_messages)
        except Exception:
            pass

    record = {
        "id": chat_id,
        "title": title,
        "messages": clean_messages,
        "document": document,
        "updatedAt": int(time.time() * 1000)
    }
    filepath = os.path.join(CHAT_DIR, f"{chat_id}.json")
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def delete_chat(chat_id: str):
    token = st.session_state.get("auth_token")
    if token and chat_id:
        try:
            backend_request("DELETE", f"/api/conversations/{chat_id}", token=token)
        except Exception:
            pass

    filepath = os.path.join(CHAT_DIR, f"{chat_id}.json")
    if os.path.exists(filepath):
        try:
            os.remove(filepath)
        except Exception:
            pass

# -----------------------------------------------------------------------------
# Tavily Search Handler
# -----------------------------------------------------------------------------

FACTUAL_LOOKUP_MARKERS = [
    "who",
    "when",
    "where",
    "which year",
    "what year",
    "according to",
    "source",
    "citation",
    "latest",
    "current",
    "recent",
    "historical",
]


def should_use_web_search(query_text, attached_doc=None):
    if attached_doc:
        return False

    text = (query_text or "").lower()

    return any(
        marker in text
        for marker in FACTUAL_LOOKUP_MARKERS
    )


def normalize_latex_delimiters(text: str) -> str:
    r"""
    Streamlit renders math using
    $...$ and $$...$$.

    Convert Qwen's \( \) and \[ \]
    delimiters into Streamlit's format.
    """

    text = re.sub(
        r"\\\[(.*?)\\\]",
        r"$$\1$$",
        text,
        flags=re.DOTALL,
    )

    text = re.sub(
        r"\\\((.*?)\\\)",
        r"$\1$",
        text,
        flags=re.DOTALL,
    )

    return text

# -----------------------------------------------------------------------------
# Vyzer UI Styling
# -----------------------------------------------------------------------------

st.html("""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">

    <style>
    :root {
        --bg-main: #131314;
        --panel-sidebar: #1a1b1c;
        --panel-card: #1e1f20;
        --panel-raised: #26272a;
        --border-color: #2e2f31;
        --border-soft: #26272a;
        --text-primary: #e3e3e3;
        --text-secondary: #9aa0a6;
        --accent-blue: #a8c7fa;
        --accent-purple: #d3e3fd;
        --accent-green: #6dd58c;

        --vyzer-gradient: linear-gradient(
            135deg,
            #4285f4 0%,
            #9b51e0 50%,
            #e91e63 100%
        );

        --radius-sm: 10px;
        --radius-md: 14px;
        --radius-lg: 20px;
    }

#MainMenu,
footer {
    display: none !important;
    visibility: hidden !important;
}

    html,
    body,
    [class*="css"] {
        font-family: 'Inter', system-ui, sans-serif;
        background-color: var(--bg-main) !important;
        color: var(--text-primary);
    }

    h1,
    h2,
    h3,
    .app-title {
        font-family: 'Google Sans', sans-serif !important;
        letter-spacing: -0.01em;
    }

    .stApp {
        background: var(--bg-main);
    }

    .block-container {
        padding-top: 1.75rem;
        padding-bottom: 2.5rem;
        max-width: 820px;
    }

    /* ---- Sidebar ---- */

    section[data-testid="stSidebar"] {
        background: var(--panel-sidebar) !important;
        border-right: 1px solid var(--border-color);
    }

    section[data-testid="stSidebar"] .block-container {
        padding-top: 1.5rem;
    }

    /* ---- Buttons ---- */

    .stButton > button,
    .stDownloadButton > button {
        border-radius: var(--radius-sm);
        border: 1px solid var(--border-color);
        background: var(--panel-raised);
        color: var(--text-primary);
        font-weight: 500;
        font-family: 'Google Sans', sans-serif;
        padding: 0.45rem 1rem;
        transition:
            border-color 0.15s ease,
            background 0.15s ease,
            transform 0.1s ease;
    }

    .stButton > button:hover,
    .stDownloadButton > button:hover {
        background: #303134;
        border-color: #4a4d51;
    }

    .stButton > button:active {
        transform: scale(0.98);
    }

    .stButton > button[kind="primary"] {
        background: var(--vyzer-gradient);
        color: #ffffff;
        border: none;
        font-weight: 600;
    }

    .stButton > button[kind="primary"]:hover {
        filter: brightness(1.08);
    }

    .stButton > button:disabled {
        opacity: 0.45;
    }

/* ---- Sidebar chat history ---- */

section[data-testid="stSidebar"] .stButton > button {
    border-radius: var(--radius-sm);
    white-space: nowrap;
    overflow: hidden;
    min-height: 40px;
}

    /* ---- Inputs / sliders / expanders ---- */

    div[data-testid="stTextInput"] input,
    div[data-testid="stTextArea"] textarea,
    div[data-baseweb="select"] > div {
        background: var(--panel-raised) !important;
        border: 1px solid var(--border-color) !important;
        border-radius: var(--radius-sm) !important;
        color: var(--text-primary) !important;
    }

    div[data-testid="stExpander"] {
        border: 1px solid var(--border-color);
        border-radius: var(--radius-md);
        background: var(--panel-card);
        overflow: hidden;
    }

    div[data-testid="stExpander"] summary {
        font-family: 'Google Sans', sans-serif;
        font-weight: 500;
    }

    .stSlider [data-baseweb="slider"] > div > div {
        background: var(--accent-blue) !important;
    }

    div[data-testid="stFileUploaderDropzone"] {
        background: var(--panel-raised);
        border: 1px dashed var(--border-color);
        border-radius: var(--radius-md);
    }

    /* ---- Header title + badges ---- */

    .app-title {
        font-size: 1.35rem;
        font-weight: 700;
        background: linear-gradient(
            90deg,
            #7cafff 0%,
            #c38fff 100%
        );
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .model-badge {
        background-color: var(--panel-raised);
        color: var(--accent-blue);
        padding: 4px 14px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 500;
        border: 1px solid var(--border-color);
        white-space: nowrap;
    }

    .model-badge.vision-on {
        color: var(--accent-green);
        border-color: #0f5223;
        background-color: #0c2b14;
    }

    /* ---- Chat messages ---- */

    div[data-testid="stChatMessage"] {
        background: var(--panel-card);
        border: 1px solid var(--border-soft);
        border-radius: var(--radius-lg);
        padding: 0.95rem 1.25rem;
        margin-bottom: 0.7rem;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);
    }

    div[data-testid="stChatInput"] {
        border: 1px solid var(--border-color);
        border-radius: 28px !important;
        background: var(--panel-card) !important;
        padding: 4px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.2);
    }

    div[data-testid="stChatInput"]:focus-within {
        border-color: #4a4d51;
    }

    /* ---- Welcome card ---- */

    .welcome-card {
        background: var(--panel-card);
        border: 1px solid var(--border-color);
        border-radius: 24px;
        padding: 3rem 2rem;
        text-align: center;
        margin: 2rem 0;
    }

    .welcome-card h2 {
        font-size: 1.9rem;
        background: linear-gradient(
            90deg,
            #a8c7fa 0%,
            #d3e3fd 50%,
            #f1f5f9 100%
        );
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.6rem;
    }

    .feature-pill {
        background: var(--panel-raised);
        padding: 6px 16px;
        border-radius: 999px;
        font-size: 0.82rem;
        color: var(--text-secondary);
        border: 1px solid var(--border-color);
    }

    /* ---- Scrollbars ---- */

    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }

    ::-webkit-scrollbar-thumb {
        background: var(--border-color);
        border-radius: 4px;
    }

    ::-webkit-scrollbar-track {
        background: transparent;
    }

    /* ---- Code blocks ---- */

    div[data-testid="stChatMessage"] pre {
        background: var(--panel-raised);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-md);
        padding: 1rem;
        margin: 0.75rem 0;
        overflow-x: auto;
        overflow-y: auto;
        max-width: 100%;
        white-space: pre;
        font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
        font-size: 0.9rem;
        line-height: 1.5;
        color: var(--text-primary);
    }

    div[data-testid="stChatMessage"] code {
        font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
        font-size: 0.9rem;
        background: var(--panel-raised);
        padding: 0.2rem 0.4rem;
        border-radius: 4px;
        border: 1px solid var(--border-color);
    }

    div[data-testid="stChatMessage"] pre code {
        background: transparent;
        padding: 0;
        border: none;
        border-radius: 0;
    }

    /* ---- Action buttons in chat messages ---- */

    div[data-testid="stChatMessage"] .stButton {
        margin-top: 0.5rem;
    }

    div[data-testid="stChatMessage"] .stButton > button {
        padding: 0.35rem 0.75rem;
        font-size: 0.85rem;
        min-width: auto;
    }

    /* ---- Responsive design ---- */

    @media (max-width: 768px) {
        .block-container {
            padding-left: 1rem;
            padding-right: 1rem;
        }

        div[data-testid="stChatMessage"] {
            padding: 0.75rem 1rem;
            border-radius: var(--radius-md);
        }

        div[data-testid="stChatMessage"] pre {
            padding: 0.75rem;
            font-size: 0.85rem;
        }

        .app-title {
            font-size: 1.2rem;
        }

        .model-badge {
            font-size: 0.72rem;
            padding: 3px 10px;
        }
    }

    @media (max-width: 480px) {
        .block-container {
            padding-left: 0.75rem;
            padding-right: 0.75rem;
        }

        div[data-testid="stChatMessage"] {
            padding: 0.6rem 0.75rem;
        }

        div[data-testid="stChatMessage"] pre {
            padding: 0.6rem;
            font-size: 0.8rem;
        }

        .app-title {
            font-size: 1.1rem;
        }
    }
    </style>
""")
# -----------------------------------------------------------------------------
# PDF & TXT Exporters
# -----------------------------------------------------------------------------

def export_as_pdf(messages, title="Chat Export"):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()

    user_style = ParagraphStyle(
        "UserStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor="#1a73e8",
        spaceAfter=4,
    )

    bot_style = ParagraphStyle(
        "BotStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=10,
        textColor="#202124",
        spaceAfter=12,
    )

    story = []

    story.append(
        Paragraph(
            title,
            styles["Title"],
        )
    )

    story.append(
        Spacer(1, 12)
    )

    for message in messages:

        role = (
            "User"
            if message["role"] == "user"
            else "Vyzer"
        )

        content = (
            message.get("content", "")
            .replace("\n", "<br/>")
        )

        style = (
            user_style
            if message["role"] == "user"
            else bot_style
        )

        story.append(
            Paragraph(
                f"{role}:",
                style,
            )
        )

        story.append(
            Paragraph(
                content,
                styles["Normal"],
            )
        )

        story.append(
            Spacer(1, 8)
        )

    doc.build(story)

    buffer.seek(0)

    return buffer.getvalue()


def export_as_txt(messages):

    output = []

    for message in messages:

        role = (
            "USER"
            if message["role"] == "user"
            else "ASSISTANT"
        )

        output.append(
            f"[{role}]:\n"
            f"{message.get('content', '')}\n"
        )

    return "\n---\n".join(output)


# -----------------------------------------------------------------------------
# Sidebar
# -----------------------------------------------------------------------------

with st.sidebar:

    st.markdown(
        """
        <div class="app-title">
            Vyzer
        </div>

        <div style="
            color:#9aa0a6;
            font-size:0.78rem;
            margin-top:4px;
            margin-bottom:12px;
        ">
            Private · Offline · Search-Grounded
        </div>
        """,
        unsafe_allow_html=True,
    )

    top_col1, top_col2 = st.columns(
        [0.75, 0.25]
    )

    if top_col1.button(
        "New Chat",
        use_container_width=True,
        type="primary",
    ):

        st.session_state.current_id = None

        st.session_state.messages = []

        st.session_state.attached_document = None

        st.session_state.pending_generation = False

        st.rerun()

    if top_col2.button(
        ":material/settings:",
        use_container_width=True,
        help="Settings",
    ):

        settings_dialog(settings)

    st.markdown("---")

    # -------------------------------------------------------------------------
    # Model Controls
    # -------------------------------------------------------------------------

    with st.expander(
        "Model Controls",
        expanded=True,
    ):

        available_models = (
            get_installed_models()
        )
        router.models = available_models

        if not available_models:
            st.error("No Ollama models are available. Start Ollama and install at least one configured general, coding, or reasoning model.")
            st.stop()

        saved_model = settings.get(
            "selected_model"
        )

        model_index = (
            available_models.index(
                saved_model
            )
            if saved_model in available_models
            else 0
        )

        selected_model = st.selectbox(
            "Preferred Model",
            available_models,
            index=model_index,
            key="preferred_model_select",
        )
        memory.model = selected_model

        st.caption(
            "Vyzer may automatically switch models for "
            "images, coding, and reasoning-heavy requests."
        )

        preset_keys = list(
            PRESET_PROMPTS.keys()
        )

        saved_preset = settings.get(
            "selected_preset"
        )

        preset_index = (
            preset_keys.index(
                saved_preset
            )
            if saved_preset in preset_keys
            else 0
        )

        selected_preset = st.selectbox(
            "Persona Preset",
            preset_keys,
            index=preset_index,
            key="persona_preset_select",
        )

        custom_prompt = st.text_area(
            "System Prompt",
            value=PRESET_PROMPTS[
                selected_preset
            ],
            height=80,
            key="system_prompt_input",
        )

        temperature = st.slider(
            "Temperature",
            0.0,
            1.0,
            float(
                settings.get(
                    "temperature",
                    0.7,
                )
            ),
            0.05,
            key="temperature_slider",
        )

        max_tokens = st.slider(
            "Max Tokens",
            256,
            4096,
            int(
                settings.get(
                    "max_tokens",
                    1024,
                )
            ),
            128,
            key="max_tokens_slider",
        )

    # -------------------------------------------------------------------------
    # Web Search
    # -------------------------------------------------------------------------

    with st.expander(
        "Web Search",
    ):

        enable_web_search = st.checkbox(
            "Tavily Grounding",
            value=bool(
                settings.get(
                    "enable_web_search",
                    False,
                )
            ),
            key="enable_web_search_checkbox",
        )

        tavily_api_key = (
            settings.get(
                "tavily_api_key"
            )
            or ENV_TAVILY_API_KEY
        )

        if (
            enable_web_search
            and not tavily_api_key
        ):

            st.warning(
                "No Tavily key set yet. "
                "Add one in Settings."
            )

    # -------------------------------------------------------------------------
    # Code Verification
    # -------------------------------------------------------------------------

    with st.expander(
        "Code Verification",
    ):

        auto_verify_code = st.checkbox(
            "Automatic executable-code verification",
            value=bool(
                settings.get(
                    "auto_verify_code",
                    os.getenv("AUTO_VERIFY_CODE", "true").strip().lower() in {"1", "true", "yes", "on"},
                )
            ),
            key="auto_verify_code_checkbox",
        )

        st.caption(
            "When enabled, complete executable code is checked "
            "against the supplied test case when one exists. "
            "Code without an expected output is only compile/execute checked; it is never called verified. "
            "If execution fails, the model may be asked to "
            "repair it."
        )

    # -------------------------------------------------------------------------
    # Save current controls
    # -------------------------------------------------------------------------

    settings.set(
        "selected_model",
        selected_model,
    )

    settings.set(
        "selected_preset",
        selected_preset,
    )

    settings.set(
        "temperature",
        temperature,
    )

    settings.set(
        "max_tokens",
        max_tokens,
    )

    settings.set(
        "enable_web_search",
        enable_web_search,
    )

    settings.set(
        "auto_verify_code",
        auto_verify_code,
    )

    settings.save()

    # -------------------------------------------------------------------------
    # Document
    # -------------------------------------------------------------------------

    with st.expander(
        "Document",
    ):

        attached = st.session_state.get(
            "attached_document"
        )

        if attached:

            st.caption(
                f"`{attached['name']}` attached "
                f"({len(attached['text']):,} chars)"
            )

            if st.button(
                "Remove Document",
                use_container_width=True,
                key="remove_document_button",
            ):

                st.session_state.attached_document = None

                st.rerun()

        else:

            st.caption(
                "Attach a PDF or TXT and the model "
                "will read its extracted text for "
                "this conversation."
            )

            uploaded_doc = st.file_uploader(
                "Upload PDF / TXT",
                type=["pdf", "txt"],
                key="document_uploader",
            )

            if uploaded_doc:

                if st.button(
                    "Attach Document",
                    use_container_width=True,
                    key="attach_document_button",
                ):

                    doc_text, doc_error = (
                        rag.extract_document(
                            uploaded_doc
                        )
                    )

                    if doc_error:

                        st.error(
                            doc_error
                        )

                    else:

                        st.session_state.attached_document = {
                            "name": uploaded_doc.name,
                            "text": doc_text,
                        }

                        if (
                            st.session_state.current_id
                            and st.session_state.messages
                        ):

                            persist_chat(
                                st.session_state.current_id,
                                st.session_state.messages,
                                document=(
                                    st.session_state
                                    .attached_document
                                ),
                            )

                        st.rerun()

    # -------------------------------------------------------------------------
    # Chat History
    # -------------------------------------------------------------------------

    st.markdown("---")

    st.caption("CHAT HISTORY")

    chats = load_chats()

    if (
        "renaming_chat_id"
        not in st.session_state
    ):

        st.session_state.renaming_chat_id = None

    for chat in chats:

        if (
            st.session_state.renaming_chat_id
            == chat["id"]
        ):

            new_title = st.text_input(
                "Rename",
                value=chat["title"],
                key=f"rename_input_{chat['id']}",
                label_visibility="collapsed",
            )

            rcol1, rcol2 = st.columns(2)

            if rcol1.button(
                "Save",
                key=f"rename_save_{chat['id']}",
                use_container_width=True,
            ):

                chat["title"] = (
                    new_title.strip()
                    or chat["title"]
                )

                filepath = os.path.join(
                    CHAT_DIR,
                    f"{chat['id']}.json",
                )

                try:

                    with open(
                        filepath,
                        "w",
                        encoding="utf-8",
                    ) as f:

                        json.dump(
                            chat,
                            f,
                            ensure_ascii=False,
                            indent=2,
                        )

                except Exception:
                    pass

                st.session_state.renaming_chat_id = None

                st.rerun()

            if rcol2.button(
                "Cancel",
                key=f"rename_cancel_{chat['id']}",
                use_container_width=True,
            ):

                st.session_state.renaming_chat_id = None

                st.rerun()

            continue

        col1, col2, col3 = st.columns(
            [0.70, 0.15, 0.15],
            gap="small",
        )

        is_active = (
            chat["id"]
            == st.session_state.current_id
        )

        if col1.button(
            chat["title"],
            key=f"load_{chat['id']}",
            use_container_width=True,
            disabled=is_active,
        ):

            st.session_state.current_id = (
                chat["id"]
            )

            st.session_state.messages = (
                chat["messages"]
            )

            st.session_state.attached_document = (
                chat.get("document")
            )

            st.rerun()

        if col2.button(
            ":material/edit:",
            key=f"ren_{chat['id']}",
            use_container_width=True,
            help="Rename chat",
        ):
            st.session_state.renaming_chat_id = chat["id"]
            st.rerun()

        if col3.button(
            ":material/delete:",
            key=f"del_{chat['id']}",
            use_container_width=True,
            help="Delete chat",
        ):
            delete_chat(chat["id"])

            if st.session_state.current_id == chat["id"]:
                st.session_state.current_id = None
                st.session_state.messages = []
                st.session_state.attached_document = None

            st.rerun()

    st.markdown("---")

    # -------------------------------------------------------------------------
    # User Info & Logout
    # -------------------------------------------------------------------------

    if st.session_state.auth_user:
        user_email = st.session_state.auth_user.get("email", "Unknown")
        st.caption(f"Logged in as: {user_email}")

        if st.button(
            "Logout",
            use_container_width=True,
        ):
            # Clear all session state on logout
            st.session_state.current_id = None
            st.session_state.messages = []
            st.session_state.attached_document = None
            st.session_state.pending_generation = False
            st.session_state.editing_message_idx = None
            st.session_state.renaming_chat_id = None
            st.session_state.last_used_model = None
            st.session_state.auth_token = None
            st.session_state.auth_user = None
            st.rerun()

    st.markdown("---")

    st.caption(
        "Vyzer · v1.2 · runs entirely on your machine"
    )
# -----------------------------------------------------------------------------
# Main Chat Area
# -----------------------------------------------------------------------------

current_title = "New Chat"

if st.session_state.messages:

    matched_chat = next(
        (
            chat
            for chat in chats
            if chat["id"]
            == st.session_state.current_id
        ),
        None,
    )

    if matched_chat:

        current_title = matched_chat["title"]

    else:

        first_user = next(
            (
                message
                for message in st.session_state.messages
                if message["role"] == "user"
            ),
            None,
        )

        if first_user:

            current_title = title_from(
                first_user["content"]
            )


# -----------------------------------------------------------------------------
# Ollama Status
# -----------------------------------------------------------------------------

if not check_ollama_running():

    st.warning(
        "Cannot reach Ollama at "
        "`localhost:11434`. Start Ollama and "
        "refresh this page. Nothing else will "
        "work until it is running."
    )


# -----------------------------------------------------------------------------
# Model Information
# -----------------------------------------------------------------------------

auto_vision_model = router.best_vision()
auto_coder_model = router.best_coder()

display_model = actual_model_label({"model": st.session_state.get("last_used_model")}, selected_model)
model_is_multimodal = router.supports_vision(display_model)


col_head1, col_head2 = st.columns(
    [0.7, 0.3]
)

with col_head1:

    st.markdown(
        f"### {current_title}"
    )

with col_head2:

    badge_class = (
        "model-badge vision-on"
        if model_is_multimodal
        else "model-badge"
    )

    st.markdown(
        f"""
        <div style="
            display:flex;
            justify-content:flex-end;
            padding-top:8px;
        ">
            <span class="{badge_class}">
                {display_model}
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# -----------------------------------------------------------------------------
# Attached Document Indicator
# -----------------------------------------------------------------------------

if st.session_state.get(
    "attached_document"
):

    attached_document = (
        st.session_state.attached_document
    )

    st.caption(
        f"`{attached_document['name']}` "
        "is attached. Every reply in this "
        "chat can reference it."
    )


# -----------------------------------------------------------------------------
# Message Display
# -----------------------------------------------------------------------------

if not st.session_state.messages:

    st.html("""
        <div class="welcome-card">

            <h2>Hello, human</h2>

            <p style="
                color:#9aa0a6;
                font-size:1rem;
                margin-bottom:1.6rem;
            ">
                How can I help you today?
                Ask questions, analyze documents,
                or use live web search.
            </p>

            <div style="
                display:flex;
                flex-wrap:wrap;
                gap:8px;
                justify-content:center;
            ">

                <span class="feature-pill">
                    Live Web Search
                </span>

                <span class="feature-pill">
                    Document Q&A
                </span>

                <span class="feature-pill">
                    Local Memory
                </span>

            </div>

        </div>
    """)

else:

    if (
        "editing_message_idx"
        not in st.session_state
    ):

        st.session_state.editing_message_idx = None

    for idx, message in enumerate(
        st.session_state.messages
    ):

        is_last = (
            idx
            == len(st.session_state.messages) - 1
        )

        avatar = (
            ":material/person:"
            if message["role"] == "user"
            else ":material/smart_toy:"
        )

        # ---------------------------------------------------------------------
        # Editing a user message
        # ---------------------------------------------------------------------

        if (
            message["role"] == "user"
            and
            st.session_state.editing_message_idx
            == idx
        ):

            with st.chat_message(
                "user",
                avatar=avatar,
            ):

                edited_text = st.text_area(
                    "Edit message",
                    value=message["content"],
                    key=f"edit_input_{idx}",
                    label_visibility="collapsed",
                )

                ecol1, ecol2 = st.columns(2)

                if ecol1.button(
                    ":material/check:",
                    key=f"edit_save_{idx}",
                    use_container_width=True,
                    type="primary",
                    help="Save and regenerate",
                ):

                    st.session_state.messages[
                        idx
                    ]["content"] = (
                        edited_text.strip()
                    )

                    # Remove the old response and
                    # everything after the edited turn.
                    st.session_state.messages = (
                        st.session_state.messages[
                            : idx + 1
                        ]
                    )

                    st.session_state.editing_message_idx = (
                        None
                    )

                    st.session_state.pending_generation = (
                        True
                    )

                    st.rerun()

                if ecol2.button(
                    ":material/close:",
                    key=f"edit_cancel_{idx}",
                    use_container_width=True,
                    help="Cancel",
                ):

                    st.session_state.editing_message_idx = (
                        None
                    )

                    st.rerun()

            continue

        # ---------------------------------------------------------------------
        # Normal message
        # ---------------------------------------------------------------------

        with st.chat_message(
            message["role"],
            avatar=avatar,
        ):

            st.markdown(
                normalize_latex_delimiters(
                    message["content"]
                )
            )

            # ---------------------------------------------------------------
            # Previously attached image
            # ---------------------------------------------------------------

            if "image_b64" in message:

                try:

                    image_bytes = base64.b64decode(
                        message["image_b64"]
                    )

                    st.image(
                        image_bytes,
                        width=280,
                    )

                except Exception:

                    pass

            # ---------------------------------------------------------------
            # Web sources
            # ---------------------------------------------------------------

            if message.get("sources"):

                with st.expander(
                    f"{len(message['sources'])} "
                    "Source(s) Consulted"
                ):

                    for source in message["sources"]:

                        title = source.get(
                            "title",
                            "Source",
                        )

                        url = source.get(
                            "url",
                            "",
                        )

                        if url:

                            st.markdown(
                                f"- [{title}]({url})"
                            )

            # ---------------------------------------------------------------
            # Verification note
            # ---------------------------------------------------------------

            verification = message.get("verification")
            if verification:
                try:
                    state = VerificationState(verification.get("state"))
                    comparison = ComparisonState(verification.get("comparison", "UNAVAILABLE"))
                    # UI status is derived only from the structured state.
                    if state is VerificationState.VERIFIED:
                        st.success("Code verification: VERIFIED — runtime output matched the supplied expected output.")
                    elif state is VerificationState.EXECUTED:
                        if comparison is ComparisonState.UNAVAILABLE:
                            st.info("Code verification: EXECUTED — correctness was not established.")
                        else:
                            st.warning("Code verification: NOT VERIFIED — runtime output did not match the supplied expected output.")
                            
                            # Show expected vs actual output clearly
                            expected_output = verification.get("expected_output", "")
                            actual_stdout = verification.get("stdout", "")
                            
                            if expected_output and actual_stdout:
                                st.markdown("**Expected output:**")
                                st.code(expected_output, language=None)
                                st.markdown("**Actual output:**")
                                st.code(actual_stdout, language=None)
                            
                            # Detailed diagnostics in expander
                            with st.expander("Verification details"):
                                st.markdown(f"**Comparison state:** {comparison.value}")
                                if verification.get("stderr"):
                                    st.markdown("**Stderr:**")
                                    st.code(verification.get("stderr"), language=None)
                                if verification.get("note"):
                                    st.markdown(f"**Note:** {verification.get('note')}")
                    
                    elif state is VerificationState.COMPILED:
                        st.info("Code verification: COMPILED — runtime correctness was not established.")
                    elif state is VerificationState.ENVIRONMENT_UNAVAILABLE:
                        st.warning("Code verification: UNAVAILABLE — required toolchain is unavailable.")
                    elif state is VerificationState.FAILED:
                        st.warning("Code verification: FAILED — correctness was not established.")
                        
                        # Show error details for failed state
                        with st.expander("Verification details"):
                            if verification.get("stderr"):
                                st.markdown("**Error output:**")
                                st.code(verification.get("stderr"), language=None)
                            if verification.get("note"):
                                st.markdown(f"**Note:** {verification.get('note')}")
                except (TypeError, ValueError):
                    st.warning("Code verification status is unavailable.")

            if message.get("verification_note"):
                st.caption(message["verification_note"])

            if message.get("model"):
                st.caption(f"Model: `{message['model']}`")

            # ---------------------------------------------------------------
            # Assistant actions
            # ---------------------------------------------------------------

            if message["role"] == "assistant":

                st.markdown('<div style="margin-top: 0.75rem;">', unsafe_allow_html=True)

                action_col1, action_col2, _ = st.columns(
                    [0.12, 0.12, 0.76],
                    gap="small",
                )

                with action_col1:

                    if st.button(
                        ":material/content_copy:",
                        key=f"copy_{idx}",
                        help="Copy",
                    ):
                        try:
                            pyperclip.copy(message["content"])
                            st.toast("Copied to clipboard!")
                        except Exception as e:
                            st.error(f"Could not copy text: {e}")

                if is_last:

                    with action_col2:

                        if st.button(
                            ":material/refresh:",
                            key=f"regen_{idx}",
                            help="Regenerate",
                        ):

                            st.session_state.messages.pop()

                            st.session_state.pending_generation = (
                                True
                            )

                            st.rerun()

                st.markdown('</div>', unsafe_allow_html=True)

            # ---------------------------------------------------------------
            # User actions
            # ---------------------------------------------------------------

            elif message["role"] == "user":

                if st.button(
                    ":material/edit:",
                    key=f"edit_{idx}",
                    help="Edit message",
                ):

                    st.session_state.editing_message_idx = (
                        idx
                    )

                    st.rerun()


# -----------------------------------------------------------------------------
# Chat Input
# -----------------------------------------------------------------------------

if st.session_state.pending_generation:

    st.caption(
        "Generating a reply. New messages are "
        "disabled until this finishes."
    )


chat_value = st.chat_input(
    "Ask Vyzer... (attach an image)",
    accept_file=True,
    file_type=[
        "png",
        "jpg",
        "jpeg",
    ],
    disabled=(
        st.session_state.pending_generation
    ),
)


prompt_input = (
    chat_value.text.strip()
    if chat_value
    else None
)

uploaded_image = (
    chat_value.files[0]
    if chat_value
    and chat_value.files
    else None
)


is_new_send = bool(
    chat_value
    and (
        prompt_input
        or uploaded_image
    )
)


# -----------------------------------------------------------------------------
# Store New User Message
# -----------------------------------------------------------------------------

if is_new_send:

    if not st.session_state.current_id:
        token = st.session_state.get("auth_token")
        if token:
            try:
                result = backend_request(
                    "POST",
                    "/api/conversations",
                    token=token,
                    payload={"title": "New chat"},
                )
                st.session_state.current_id = result["conversation"]["id"]
            except Exception:
                st.session_state.current_id = str(uuid.uuid4())
        else:
            st.session_state.current_id = str(uuid.uuid4())

    user_message = {
        "role": "user",
        "content": (
            prompt_input
            or "(image attached)"
        ),
    }

    if uploaded_image:

        user_message[
            "image_b64"
        ] = image_to_base64(
            uploaded_image
        )

        user_message[
            "image_mime"
        ] = (
            uploaded_image.type
            or "image/jpeg"
        )

    st.session_state.messages.append(
        user_message
    )

    with st.chat_message(
        "user",
        avatar=":material/person:",
    ):

        st.markdown(
            user_message["content"]
        )

        if uploaded_image:

            st.image(
                uploaded_image,
                width=280,
            )

    st.session_state.pending_generation = True

    st.rerun()
# -----------------------------------------------------------------------------
# Response Generation
# -----------------------------------------------------------------------------

if st.session_state.pending_generation:

    current_user_msg = st.session_state.messages[-1]

    query_text = current_user_msg.get(
        "content",
        "",
    )

    has_image = bool(
        current_user_msg.get("image_b64")
    )

    # ---------------------------------------------------------
    # Intelligent model routing: one authoritative decision per request.
    # ---------------------------------------------------------
    vision_model = None
    extracted_image_text = None
    if has_image:
        vision_model = router.best_vision()
        if not vision_model:
            st.error("No vision-capable Ollama model is available. Configure VYZER_VISION_MODEL or install a supported vision model.")
            st.stop()
        st.caption(f"Reading image with `{vision_model}`...")
        extracted_image_text = extract_image_text(
            vision_model=vision_model,
            image_b64=current_user_msg["image_b64"],
            image_mime=current_user_msg.get("image_mime", "image/jpeg"),
            user_instruction=query_text,
        )
        active_model = router.choose_image_answer_model(extracted_image_text)
    else:
        active_model = router.choose_model(
            selected_model=selected_model,
            prompt=query_text,
            has_image=False,
        )

    if not active_model:
        st.error("Vyzer could not find a suitable local model for this request. Check the Ollama model configuration.")
        st.stop()

    st.session_state.last_used_model = active_model
    memory.model = active_model

    if vision_model:
        if router.is_coder(active_model):
            st.caption(f"Image read with `{vision_model}`; solving with `{active_model}`.")
        else:
            st.caption(f"Image read with `{vision_model}`; answering with `{active_model}`.")
    elif router.is_coder(active_model):
        st.caption(f"Using `{active_model}` for coding.")
    elif router.is_reasoning_model(active_model):
        st.caption(f"Using `{active_model}` for reasoning.")
    else:
        st.caption(f"Using `{active_model}`.")

    # -------------------------------------------------------------------------
    # Attached Document
    # -------------------------------------------------------------------------

    attached_doc = (
        st.session_state.get(
            "attached_document"
        )
    )

    doc_context = ""

    if attached_doc:

        doc_context = attached_doc.get(
            "text",
            "",
        )

    # -------------------------------------------------------------------------
    # Web Search
    # -------------------------------------------------------------------------

    web_context = ""
    web_sources = []
    web_search_error = ""

    if (
        enable_web_search
        and query_text
        and should_use_web_search(
            query_text,
            attached_doc=attached_doc,
        )
    ):

        with st.spinner(
            "Searching the web..."
        ):

            (
                web_context,
                web_sources,
                web_search_error,
            ) = rag.search(
                query_text
            )

        if web_search_error:

            st.warning(
                f"Web search: {web_search_error}"
            )

    # -------------------------------------------------------------------------
    # System Prompt
    # -------------------------------------------------------------------------

    today_str = datetime.now().strftime(
        "%A, %B %d, %Y"
    )

    augmented_system_prompt = (
        f"{custom_prompt}\n\n"
        f"Today's date is {today_str}.\n\n"
        "For current events, prices, scores, "
        "or current positions, use the supplied "
        "live web results when available.\n\n"
        "For mathematics and notation, use "
        "LaTeX with $...$ for inline math and "
        "$$...$$ for display math.\n\n"
        "For academic and factual questions:\n\n"
        "- Prefer established facts over plausible-sounding explanations.\n"
        "- Do not create terminology or definitions.\n"
        "- When a term has a specific technical meaning, use that meaning.\n"
        "- Distinguish facts from interpretations or hypotheses.\n"
        "- If you are uncertain about a specialized fact, explicitly say so "
        "rather than confidently guessing.\n\n"
        "Before finalizing an answer, silently check that:\n\n"
        "- Every claim is consistent with the other claims in your answer.\n"
        "- Every requested part of the question has been answered.\n"
        "- Examples actually support the explanation.\n"
        "- Definitions are used consistently.\n"
        "- Calculations and units are correct.\n"
        "- Step-by-step procedures reach the stated final result.\n"
        "- Do not state uncertain information as fact."
    )

    if router.is_coder(active_model):
        # Check if the user specified an expected output (competitive programming format)
        test_input, expected_output = verifier.extract_test_cases(query_text)
        
        if expected_output is not None:
            augmented_system_prompt += (
                "You are a competitive programming expert.\n\n"
                "The user has specified an expected output for their test case.\n\n"
                "CRITICAL OUTPUT REQUIREMENTS:\n"
                "- DO NOT print interactive prompts such as 'Enter an integer:' or 'Enter n:'\n"
                "- DO NOT print explanatory labels such as 'Factorial of 5! is:' or 'Result:'\n"
                "- Output ONLY the raw result that matches the expected output format\n"
                "- Your program must produce EXACTLY the expected output when run with the given input\n"
                "- Treat this as a competitive programming problem, not an interactive educational program\n\n"
                "Accuracy is more important than speed.\n\n"
                "Before returning your answer, mentally verify the complete solution.\n\n"
                "For algorithms:\n" 
                "- Verify the algorithm logically, not merely syntactically."
                "- Check the algorithm against small counterexamples."
                "- Check edge cases."
                "- Verify that every requirement in the question is satisfied."
                "- Verify that the implementation matches the explanation."
                "- Verify time and space complexity."
                "- Never claim O(n+m) if the implementation actually requires O(n²)."
                "- For topological sorting, verify that every directed edge u→v has u before v."
                "- For cycle detection, verify both cyclic and acyclic cases."
                "- Do not hard-code an example when a general solution is requested."

                "For code:\n\n"
                "- Check includes/imports."
                "- Check variable scope."
                "- Check return values."
                "- Check empty inputs and boundary cases."
                "- Check compilation mentally before responding."
                "- Do not output incomplete code."

                "Take additional time internally if necessary."

                "Return the complete answer. ")
        else:
            augmented_system_prompt += (
                "You are a careful programming expert.\n\n"
                "Accuracy is more important than speed.\n\n"
                "Before returning your answer, mentally verify the complete solution.\n\n"
                "For algorithms:\n" 
                "- Verify the algorithm logically, not merely syntactically."
                "- Check the algorithm against small counterexamples."
                "- Check edge cases."
                "- Verify that every requirement in the question is satisfied."
                "- Verify that the implementation matches the explanation."
                "- Verify time and space complexity."
                "- Never claim O(n+m) if the implementation actually requires O(n²)."
                "- For topological sorting, verify that every directed edge u→v has u before v."
                "- For cycle detection, verify both cyclic and acyclic cases."
                "- Do not hard-code an example when a general solution is requested."

                "For code:\n\n"
                "- Check includes/imports."
                "- Check variable scope."
                "- Check return values."
                "- Check empty inputs and boundary cases."
                "- Check compilation mentally before responding."
                "- Do not output incomplete code."

                "Take additional time internally if necessary."

                "Return the complete answer. ")

    augmented_system_prompt = (
        rag.build_system_prompt(
            base_prompt=augmented_system_prompt,
            web_context=web_context,
            doc_name=(
                attached_doc["name"]
                if attached_doc
                else ""
            ),
            doc_text=doc_context,
        )
    )

    # -------------------------------------------------------------------------
    # Prepare conversation history
    # -------------------------------------------------------------------------

    if extracted_image_text:

        query_text = extracted_image_text

        history = [
            {
                "role": "user",
                "content": extracted_image_text,
            }
        ]

    else:

        history = []

        last_image_index = -1

        for index, message in enumerate(
            st.session_state.messages
        ):

            if message.get("image_b64"):

                last_image_index = index

        for index, message in enumerate(
            st.session_state.messages
        ):

            role = message.get(
                "role",
                "user",
            )

            content = message.get(
                "content",
                "",
            )

            if (
                message.get("image_b64")
                and index == last_image_index
                and vision_model
            ):

                mime = message.get(
                    "image_mime",
                    "image/jpeg",
                )

                history.append(
                    {
                        "role": role,
                        "content": [
                            {
                                "type": "text",
                                "text": content,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": (
                                        f"data:{mime};base64,"
                                        f"{message['image_b64']}"
                                    )
                                },
                            },
                        ],
                    }
                )

            elif message.get("image_b64"):

                history.append(
                    {
                        "role": role,
                        "content": (
                            f"{content} "
                            "[image attached earlier]"
                        ),
                    }
                )

            else:

                history.append(
                    {
                        "role": role,
                        "content": content,
                    }
                )

    # -------------------------------------------------------------------------
    # Build context through ConversationMemory
    # -------------------------------------------------------------------------

    memory.model = active_model

    api_messages = memory.build_messages(
        system_prompt=augmented_system_prompt,
        history=history,
    )

    # -------------------------------------------------------------------------
    # Generate response
    # -------------------------------------------------------------------------

    with st.chat_message(
        "assistant",
        avatar=":material/smart_toy:",
    ):

        message_placeholder = st.empty()

        full_reply = ""

        verification_note = ""

        try:

            # ---------------------------------------------------------
            # Automatic generation profile
            # ---------------------------------------------------------

            generation = router.generation_profile(
                prompt=query_text,
                model=active_model,
                has_image=False if extracted_image_text else has_image,
            )

            temperature = generation["temperature"]
            max_tokens = generation["max_tokens"]

            buffer = ""
            last_flush = time.monotonic()

            for (
                chunk,
                accumulated,
            ) in llm.safe_stream_chat(
                model=active_model,
                messages=api_messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ):

                full_reply = accumulated
                buffer += chunk

                now = time.monotonic()
                should_flush = (
                    len(buffer) >= 32
                    or (now - last_flush) >= 0.2
                )

                if should_flush:
                    message_placeholder.markdown(
                        normalize_latex_delimiters(
                            full_reply
                        )
                        + "▌"
                    )
                    buffer = ""
                    last_flush = now

            message_placeholder.markdown(
                normalize_latex_delimiters(
                    full_reply
                )
            )

            # -----------------------------------------------------------------
            # Automatic incomplete-response recovery
            # -----------------------------------------------------------------

            from services.response_completion import ResponseCompletion

            continuation_attempts = 0
            MAX_CONTINUATIONS = 2

            while (
                ResponseCompletion.looks_incomplete(full_reply)
                and continuation_attempts < MAX_CONTINUATIONS
            ):

                continuation_attempts += 1

                message_placeholder.markdown(
                    normalize_latex_delimiters(
                        full_reply
                    )
                    + "\n\n"
                    + "..."
                )

                try:

                    continuation = llm.continue_chat(
                        model=active_model,
                        messages=api_messages,
                        previous_reply=full_reply,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        num_ctx=4096,
                    )

                except Exception:
                    break

                if not continuation:
                    break

                continuation = continuation.strip()

                if not continuation:
                    break

                full_reply += "\n\n" + continuation

                message_placeholder.markdown(
                    normalize_latex_delimiters(
                        full_reply
                    )
                )

            # -----------------------------------------------------------------
            # Authoritative executable verification
            # -----------------------------------------------------------------
            verification_result = None
            executable_verification_attempted = False

            if auto_verify_code:
                application_verification = verify_generated_answer(
                    router=router,
                    verifier=verifier,
                    model=active_model,
                    api_messages=api_messages,
                    prompt=query_text,
                    reply=full_reply,
                    temperature=0.2,
                    max_tokens=2048,
                    num_ctx=4096,
                )
                executable_verification_attempted = application_verification.attempted
                verification_result = application_verification.result

                if verification_result is not None:
                    verification_note = verification_result.note
                    label, diagnostic = status_text(verification_result)
                    if verification_result.state is VerificationState.VERIFIED:
                        st.success(f"Code verification: {label} — {diagnostic}")
                    elif verification_result.state in {VerificationState.FAILED, VerificationState.ENVIRONMENT_UNAVAILABLE}:
                        st.warning(f"Code verification: {label} — {diagnostic}")
                    else:
                        st.info(f"Code verification: {label} — {diagnostic}")

                    if (
                        verification_result.state is VerificationState.VERIFIED
                        and verification_result.verified
                        and verification_result.repaired
                        and verification_result.reply.strip() != full_reply.strip()
                    ):
                        full_reply = verification_result.reply
                        message_placeholder.markdown(normalize_latex_delimiters(full_reply))

            # -----------------------------------------------------------------
            # Optional reasoning / answer verification
            # -----------------------------------------------------------------

            response_verification_note = ""

            router.current_model = active_model
            if (
                full_reply
                and should_run_response_verifier(executable_verification_attempted, router, query_text)
                and response_verifier.should_verify(
                    query_text,
                    full_reply,
                )
            ):

                st.caption("Checking answer...")

                (
                    verified_reply,
                    response_verification_note,
                ) = response_verifier.verify(
                    model=active_model,
                    prompt=query_text,
                    response=full_reply,
                    temperature=0.1,
                )

                if (
                    verified_reply
                    and verified_reply.strip()
                    and verified_reply.strip() != full_reply.strip()
                    and response_verification_note
                    and response_verification_note.startswith(
                        "Answer checked and corrected."
                    )
                ):

                    full_reply = verified_reply

                    message_placeholder.markdown(
                        normalize_latex_delimiters(
                            full_reply
                        )
                    )

            # -----------------------------------------------------------------
            # Save assistant response
            # -----------------------------------------------------------------

            assistant_msg = {
                "role": "assistant",
                "content": full_reply,
                "model": active_model,
            }

            if verification_result is not None:
                assistant_msg["verification"] = {
                    "state": verification_result.state.value,
                    "comparison": verification_result.comparison.value,
                    "language": verification_result.language,
                    "repaired": verification_result.repaired,
                    "attempts": verification_result.attempts,
                    "test_input_present": verification_result.test_input_present,
                    "expected_output_present": verification_result.expected_output_present,
                    "note": verification_result.note,
                }

            if web_sources:

                assistant_msg[
                    "sources"
                ] = web_sources

            if response_verification_note:

                if verification_note:

                    verification_note += (
                        "\n\n"
                        + response_verification_note
                    )

                else:

                    verification_note = response_verification_note

            if verification_note:

                assistant_msg[
                    "verification_note"
                ] = verification_note

            st.session_state.messages.append(
                assistant_msg
            )

            persist_chat(
                st.session_state.current_id,
                st.session_state.messages,
                document=attached_doc,
            )

            st.session_state.pending_generation = (
                False
            )

            st.rerun()

        except Exception as e:

            st.session_state.pending_generation = (
                False
            )

            error_text = str(e)

            if (
                "context" in error_text.lower()
                or "4096" in error_text
            ):

                st.error(
                    "The conversation is still too "
                    "large for the model's current "
                    "context window."
                )

                st.caption(
                    "Vyzer automatically limits "
                    "conversation history to protect "
                    "your laptop's available memory. "
                    "Start a new chat if the problem "
                    "continues."
                )

            else:

                st.error(
                    f"Error communicating with "
                    f"local model ({active_model}): "
                    f"{error_text}"
                )

            if (
                "timed out"
                in error_text.lower()
            ):

                st.caption(
                    "The local model timed out. "
                    "It may still be loading or "
                    "your machine may be running "
                    "out of available memory. "
                    "Check `ollama ps` or try a "
                    "smaller model."
                )
# -----------------------------------------------------------------------------
# Chat Export
# -----------------------------------------------------------------------------

if st.session_state.messages:

    st.markdown("---")

    export_col1, export_col2, export_col3 = st.columns(
        [0.6, 0.2, 0.2]
    )

    export_col1.caption(
        f"Messages: {len(st.session_state.messages)}"
    )

    pdf_bytes = export_as_pdf(
        st.session_state.messages,
        current_title,
    )

    txt_data = export_as_txt(
        st.session_state.messages,
    )

    export_col2.download_button(
    ":material/picture_as_pdf:",
    pdf_bytes,
    f"{current_title}.pdf",
    "application/pdf",
    use_container_width=True,
    icon=None,
    help="Export as PDF",
)
    export_col3.download_button(
    ":material/description:",
    txt_data,
    f"{current_title}.txt",
    "text/plain",
    use_container_width=True,
    icon=None,
    help="Export as TXT",
)
