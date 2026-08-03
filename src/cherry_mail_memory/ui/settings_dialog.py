from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from cherry_mail_memory.config import AppSettings


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ตั้งค่า Cherry Mail Memory")
        self.setMinimumWidth(680)
        self._original = settings

        root = QVBoxLayout(self)
        root.addWidget(self._sync_group(settings))
        root.addWidget(self._ai_group(settings))

        note = QLabel(
            "API key จะเก็บในไฟล์ settings ของเครื่องนี้ กรุณาใช้ key ที่จำกัดสิทธิ์ "
            "หรือใช้ local endpoint เช่น Ollama/vLLM"
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8b98a9;")
        root.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _sync_group(self, settings: AppSettings) -> QGroupBox:
        group = QGroupBox("Gmail และ Archive")
        form = QFormLayout(group)

        self.auto_sync = QCheckBox("อัปเดตเมลอัตโนมัติขณะเปิดแอป")
        self.auto_sync.setChecked(settings.auto_sync)
        form.addRow(self.auto_sync)

        self.sync_interval = QSpinBox()
        self.sync_interval.setRange(1, 1440)
        self.sync_interval.setSuffix(" นาที")
        self.sync_interval.setValue(settings.sync_interval_minutes)
        form.addRow("ช่วงเวลาอัปเดต", self.sync_interval)

        self.store_raw = QCheckBox("เก็บ MIME ดิบใน SQLite เพื่อ export เป็น .eml ได้")
        self.store_raw.setChecked(settings.store_raw_mime)
        form.addRow(self.store_raw)

        self.download_attachments = QCheckBox("แยกไฟล์แนบออกมาเก็บบนดิสก์ด้วย")
        self.download_attachments.setChecked(settings.download_attachments)
        form.addRow(self.download_attachments)

        self.max_attachment = QSpinBox()
        self.max_attachment.setRange(0, 1024)
        self.max_attachment.setSpecialValueText("ไม่จำกัด")
        self.max_attachment.setSuffix(" MB/ไฟล์")
        self.max_attachment.setValue(settings.max_attachment_mb)
        form.addRow("ขนาดไฟล์แนบสูงสุด", self.max_attachment)
        return group

    def _ai_group(self, settings: AppSettings) -> QGroupBox:
        group = QGroupBox("Local AI / OpenAI-compatible API")
        form = QFormLayout(group)

        self.auto_index = QCheckBox("สร้าง embedding ให้เมลใหม่หลัง sync")
        self.auto_index.setChecked(settings.auto_index)
        form.addRow(self.auto_index)

        self.llm_url = QLineEdit(settings.llm_base_url)
        self.llm_url.setPlaceholderText("http://127.0.0.1:11434/v1")
        form.addRow("Chat base URL", self.llm_url)

        self.llm_model = QLineEdit(settings.llm_model)
        self.llm_model.setPlaceholderText("เช่น qwen3.5:9b หรือ model id จาก vLLM")
        form.addRow("Chat model", self.llm_model)

        self.llm_key = QLineEdit(settings.llm_api_key)
        self.llm_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.llm_key.setPlaceholderText("เว้นว่างได้สำหรับ local server")
        form.addRow("Chat API key", self.llm_key)

        self.embedding_url = QLineEdit(settings.embedding_base_url)
        self.embedding_url.setPlaceholderText("http://127.0.0.1:11434/v1")
        form.addRow("Embedding base URL", self.embedding_url)

        self.embedding_model = QLineEdit(settings.embedding_model)
        self.embedding_model.setPlaceholderText("เช่น qwen3-embedding หรือ nomic-embed-text")
        form.addRow("Embedding model", self.embedding_model)

        self.embedding_key = QLineEdit(settings.embedding_api_key)
        self.embedding_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.embedding_key.setPlaceholderText("เว้นว่างได้สำหรับ local server")
        form.addRow("Embedding API key", self.embedding_key)

        self.top_k = QSpinBox()
        self.top_k.setRange(1, 30)
        self.top_k.setValue(settings.retrieval_top_k)
        form.addRow("จำนวนหลักฐานต่อคำถาม", self.top_k)
        return group

    def settings(self) -> AppSettings:
        updated = replace(
            self._original,
            sync_interval_minutes=self.sync_interval.value(),
            auto_sync=self.auto_sync.isChecked(),
            auto_index=self.auto_index.isChecked(),
            store_raw_mime=self.store_raw.isChecked(),
            download_attachments=self.download_attachments.isChecked(),
            max_attachment_mb=self.max_attachment.value(),
            llm_base_url=self.llm_url.text().strip(),
            llm_model=self.llm_model.text().strip(),
            llm_api_key=self.llm_key.text().strip(),
            embedding_base_url=self.embedding_url.text().strip(),
            embedding_model=self.embedding_model.text().strip(),
            embedding_api_key=self.embedding_key.text().strip(),
            retrieval_top_k=self.top_k.value(),
        )
        updated.validate()
        return updated
