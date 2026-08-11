import os
import json
import streamlit as st


class SettingsManager:
    def __init__(self, settings_file):
        self.settings_file = settings_file
        self.settings = self.load()

    def load(self):
        try:
            with open(self.settings_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self):
        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)

        with open(self.settings_file, "w", encoding="utf-8") as f:
            json.dump(
                self.settings,
                f,
                indent=2,
                ensure_ascii=False,
            )

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings[key] = value


def settings_dialog(manager):

    st.subheader("Settings")

    tavily_key = st.text_input(
        "Tavily API Key",
        value=manager.get("tavily_api_key", ""),
        type="password",
    )

    temperature = st.slider(
        "Temperature",
        0.0,
        1.0,
        float(manager.get("temperature", 0.4)),
        0.05,
    )

    max_tokens = st.slider(
        "Max Tokens",
        256,
        4096,
        int(manager.get("max_tokens", 1024)),
        128,
    )

    enable_web = st.checkbox(
        "Enable Web Search",
        value=bool(manager.get("enable_web_search", False)),
    )

    auto_verify = st.checkbox(
        "Automatic executable-code verification",
        value=bool(manager.get("auto_verify_code", True)),
    )

    personas = [
        "General Assistant"
    ]

    current = manager.get(
        "selected_preset",
        personas[0],
    )

    preset = st.selectbox(
        "Persona",
        personas,
        index=personas.index(current)
        if current in personas
        else 0,
    )

    if st.button(
        "Save Settings",
        use_container_width=True,
    ):

        manager.set(
            "tavily_api_key",
            tavily_key.strip(),
        )

        manager.set(
            "temperature",
            temperature,
        )

        manager.set(
            "max_tokens",
            max_tokens,
        )

        manager.set(
            "enable_web_search",
            enable_web,
        )

        manager.set(
            "auto_verify_code",
            auto_verify,
        )

        manager.set(
            "selected_preset",
            preset,
        )

        manager.save()

        st.success("Settings saved.")

        st.rerun()
