from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import urlparse

import httpx


class AIServiceError(RuntimeError):
    pass


@dataclass(slots=True)
class OpenAICompatibleClient:
    base_url: str
    api_key: str = ""
    timeout_seconds: int = 180

    def __post_init__(self) -> None:
        self.base_url = self._normalize_base_url(self.base_url)

    @staticmethod
    def _normalize_base_url(value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("AI base URL is empty")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid AI base URL: {value}")
        return value if value.endswith("/v1") else f"{value}/v1"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=self._headers(), json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise AIServiceError(
                f"AI service returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise AIServiceError(f"Unable to call AI service at {url}: {exc}") from exc
        if not isinstance(data, dict):
            raise AIServiceError("AI service returned an invalid JSON object")
        return data

    def embeddings(self, model: str, inputs: Sequence[str]) -> list[list[float]]:
        if not model.strip():
            raise ValueError("Embedding model is empty")
        if not inputs:
            return []
        payload = {"model": model, "input": list(inputs)}
        data = self._post("embeddings", payload)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise AIServiceError("Embedding response does not contain a data array")
        ordered = sorted(rows, key=lambda item: int(item.get("index", 0)))
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise AIServiceError("Embedding response contains an empty vector")
            vectors.append([float(value) for value in vector])
        if len(vectors) != len(inputs):
            raise AIServiceError(
                f"Embedding count mismatch: expected {len(inputs)}, received {len(vectors)}"
            )
        return vectors

    def chat(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1400,
    ) -> str:
        if not model.strip():
            raise ValueError("Chat model is empty")
        payload = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        data = self._post("chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIServiceError("Chat response does not contain choices")
        content = choices[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise AIServiceError("Chat response contains no text")
        return content.strip()
