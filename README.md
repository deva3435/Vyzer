**Vyzer**

**A local-first AI assistant built to make the most out of limited hardware.**

I wanted an AI assistant that could handle coding, reasoning, documents, and everyday questions without requiring a powerful GPU or constantly relying on paid cloud APIs.

So I built **Vyzer** around small local Ollama models and automatic model routing. Instead of using one model for everything, Vyzer chooses a suitable model based on the task.

## What it does

-  Automatic routing between local AI models
-  Executable code generation and verification
-  Document and web-based context
-  Conversation memory and persistent chat history
-  User authentication and isolated conversations
-  FastAPI backend + Streamlit frontend
-  SQLite locally, PostgreSQL for production

### Code verification

For programming questions with a concrete expected output, Vyzer can actually run the generated code and compare the result against the expected output.

Verification is **fail-closed**: the UI cannot claim that code is verified based on the model's response alone.


                                                          User request
                                                               ↓
                                                          Model routing
                                                               ↓
                                                          Code generation
                                                               ↓
                                                          Compile / execute
                                                               ↓
                                                          Compare actual vs expected output
                                                               ↓
                                                          VERIFIED / FAILED / EXECUTED

Architecture
                ┌──────────────┐
                │   Streamlit  │
                │   Frontend   │
                └──────┬───────┘
                       │
             ┌─────────┴─────────┐
             ↓                   ↓
        Ollama models        FastAPI
                                │
                                ↓
                         SQLite / PostgreSQL
                         

Run locally
Requirements:

Python
Ollama
At least one compatible local model
Language toolchains only for languages you want to verify

After installing dependencies, the project can be started with:

.\start_vyzer.ps1

The launcher starts the backend, Streamlit, performs the database setup, checks Ollama, and opens Vyzer in the browser.


Tech Stack

Frontend: Streamlit
Backend: FastAPI
AI: Ollama
Database: SQLite / PostgreSQL
Authentication: JWT + Argon2
Migrations: Alembic
Testing: Pytest
Languages verified: Python, C, C++, Java, JavaScript, TypeScript, Go, Rust*
*subject to the required compiler/toolchain being installed.

Security note

The local code verifier is not a security sandbox. It is intended for trusted/local use. Public deployment of arbitrary code execution requires a proper isolation layer such as containers, VMs, or a dedicated job runner.
