# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import logging
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class LLMProvider(Enum):
    """Supported LLM providers."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


class LLMClient:
    """Unified client for generating explanations via LLM providers."""

    def __init__(
        self,
        provider: str,
        api_key: str,
        model: str,
        base_url: str = "",
    ) -> None:
        self._provider = LLMProvider(provider)
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    def explain(self, system_prompt: str, user_prompt: str) -> str:
        """Send a prompt to the configured LLM and return the response text."""
        if self._provider == LLMProvider.OPENAI:
            return self._call_openai(system_prompt, user_prompt)
        if self._provider == LLMProvider.ANTHROPIC:
            return self._call_anthropic(system_prompt, user_prompt)
        if self._provider == LLMProvider.LOCAL:
            return self._call_local(system_prompt, user_prompt)

        msg = f"Unsupported LLM provider: {self._provider}"
        raise ValueError(msg)

    def _call_openai(self, system_prompt: str, user_prompt: str) -> str:
        import openai

        client = openai.OpenAI(api_key=self._api_key)
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        content = response.choices[0].message.content
        return content or ""

    def _call_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        block = response.content[0]
        return block.text if hasattr(block, "text") else str(block)

    def _call_local(self, system_prompt: str, user_prompt: str) -> str:
        """Call a local OpenAI-compatible endpoint (e.g. Ollama)."""
        base = self._base_url.rstrip("/")
        if base.endswith("/openai") or base.endswith("/v1"):
            url = base + "/chat/completions"
        else:
            url = base + "/v1/chat/completions"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
