from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir, user_data_dir, user_log_dir


APP_NAME = "CherryMailMemory"
APP_AUTHOR = "paddman"


@dataclass(slots=True)
class AppPaths:
    data_dir: Path
    config_dir: Path
    log_dir: Path
    database_path: Path
    token_dir: Path
    attachment_dir: Path
    config_path: Path

    @classmethod
    def create(cls) -> "AppPaths":
        data_dir = Path(user_data_dir(APP_NAME, APP_AUTHOR))
        config_dir = Path(user_config_dir(APP_NAME, APP_AUTHOR))
        log_dir = Path(user_log_dir(APP_NAME, APP_AUTHOR))
        token_dir = data_dir / "tokens"
        attachment_dir = data_dir / "attachments"
        for directory in (data_dir, config_dir, log_dir, token_dir, attachment_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_dir,
            config_dir=config_dir,
            log_dir=log_dir,
            database_path=data_dir / "mail_memory.db",
            token_dir=token_dir,
            attachment_dir=attachment_dir,
            config_path=config_dir / "settings.json",
        )


@dataclass(slots=True)
class AppSettings:
    sync_interval_minutes: int = 10
    auto_sync: bool = True
    auto_index: bool = True
    store_raw_mime: bool = True
    download_attachments: bool = False
    max_attachment_mb: int = 50
    batch_size: int = 20
    chunk_size: int = 1600
    chunk_overlap: int = 200
    retrieval_top_k: int = 8
    request_timeout_seconds: int = 180
    llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_model: str = ""
    llm_api_key: str = ""
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_model: str = ""
    embedding_api_key: str = ""

    def validate(self) -> None:
        self.sync_interval_minutes = max(1, min(int(self.sync_interval_minutes), 1440))
        self.max_attachment_mb = max(0, min(int(self.max_attachment_mb), 1024))
        self.batch_size = max(1, min(int(self.batch_size), 50))
        self.chunk_size = max(400, min(int(self.chunk_size), 12000))
        self.chunk_overlap = max(0, min(int(self.chunk_overlap), self.chunk_size // 2))
        self.retrieval_top_k = max(1, min(int(self.retrieval_top_k), 30))
        self.request_timeout_seconds = max(10, min(int(self.request_timeout_seconds), 900))
        self.llm_base_url = self.llm_base_url.strip()
        self.llm_model = self.llm_model.strip()
        self.embedding_base_url = self.embedding_base_url.strip()
        self.embedding_model = self.embedding_model.strip()


class SettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings()
            self.save(settings)
            return settings

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return AppSettings()

        allowed = {item.name for item in fields(AppSettings)}
        clean: dict[str, Any] = {key: value for key, value in payload.items() if key in allowed}
        settings = AppSettings(**clean)
        settings.validate()
        return settings

    def save(self, settings: AppSettings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(asdict(settings), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
