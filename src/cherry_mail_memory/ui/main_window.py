from __future__ import annotations

import html
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from cherry_mail_memory.config import AppPaths, AppSettings, SettingsStore
from cherry_mail_memory.database import Database
from cherry_mail_memory.models import AIAnswer, RetrievedChunk
from cherry_mail_memory.rag import RAGEngine
from cherry_mail_memory.sync_service import SyncService
from cherry_mail_memory.ui.settings_dialog import SettingsDialog
from cherry_mail_memory.ui.workers import TaskWorker


LOGGER = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        settings_store: SettingsStore,
        settings: AppSettings,
        paths: AppPaths,
    ) -> None:
        super().__init__()
        self.database = database
        self.settings_store = settings_store
        self.settings = settings
        self.paths = paths
        self.sync_service = SyncService(database, settings, paths)
        self.rag = RAGEngine(database, settings)
        self.workers: set[TaskWorker] = set()
        self.sync_running = False
        self.chat_sessions: dict[int, int] = {}
        self.current_sources: list[RetrievedChunk] = []

        self.setWindowTitle("Cherry Mail Memory")
        self.resize(1320, 840)
        self.setMinimumSize(1040, 680)
        self._build_ui()
        self._apply_style()

        self.auto_sync_timer = QTimer(self)
        self.auto_sync_timer.timeout.connect(self._auto_sync)
        self._restart_timer()
        self.reload_accounts()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("Cherry Mail Memory")
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)

        header.addWidget(QLabel("บัญชี"))
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(250)
        self.account_combo.currentIndexChanged.connect(self._account_changed)
        header.addWidget(self.account_combo)

        self.add_account_button = QPushButton("+ เชื่อม Gmail")
        self.add_account_button.clicked.connect(self.connect_gmail)
        header.addWidget(self.add_account_button)

        self.sync_button = QPushButton("อัปเดตเมล")
        self.sync_button.clicked.connect(lambda: self.start_sync(force_full=False))
        header.addWidget(self.sync_button)

        self.full_sync_button = QPushButton("Full sync")
        self.full_sync_button.clicked.connect(lambda: self.start_sync(force_full=True))
        header.addWidget(self.full_sync_button)

        self.index_button = QPushButton("สร้าง AI Index")
        self.index_button.clicked.connect(self.start_indexing)
        header.addWidget(self.index_button)
        root.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._mail_tab(), "ค้นหาอีเมล")
        self.tabs.addTab(self._ai_tab(), "AI Memory")
        self.tabs.addTab(self._system_tab(), "ระบบและตั้งค่า")
        root.addWidget(self.tabs, 1)

        status_layout = QHBoxLayout()
        self.status_label = QLabel("พร้อม")
        self.status_label.setObjectName("status")
        status_layout.addWidget(self.status_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(320)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.progress_bar)
        root.addLayout(status_layout)

        self.setCentralWidget(central)

    def _mail_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 10, 4, 4)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            'ค้นข้อความ หรือใช้ from: subject: to: label: after:2026-01-01 before:2026-12-31'
        )
        self.search_input.returnPressed.connect(self.search_mail)
        search_row.addWidget(self.search_input, 1)
        search_button = QPushButton("ค้นหา")
        search_button.clicked.connect(self.search_mail)
        search_row.addWidget(search_button)
        self.export_button = QPushButton("Export .eml")
        self.export_button.clicked.connect(self.export_current_message)
        self.export_button.setEnabled(False)
        search_row.addWidget(self.export_button)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.mail_table = QTableWidget(0, 4)
        self.mail_table.setHorizontalHeaderLabels(["วันที่", "จาก", "หัวข้อ", "ไฟล์แนบ"])
        self.mail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mail_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mail_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mail_table.verticalHeader().setVisible(False)
        self.mail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.mail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.mail_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.mail_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.mail_table.itemSelectionChanged.connect(self._mail_selected)
        splitter.addWidget(self.mail_table)

        self.mail_view = QTextBrowser()
        self.mail_view.setOpenExternalLinks(False)
        self.mail_view.setOpenLinks(False)
        self.mail_view.setPlaceholderText("เลือกอีเมลเพื่ออ่าน")
        splitter.addWidget(self.mail_view)
        splitter.setSizes([570, 700])
        layout.addWidget(splitter, 1)
        return page

    def _ai_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 10, 4, 4)

        info = QLabel(
            "ถามจากอีเมลที่เก็บไว้ ระบบจะค้นแบบ hybrid search แล้วส่งเฉพาะหลักฐานที่เกี่ยวข้องให้โมเดล"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setHtml(
            "<h3>AI Memory</h3><p>ตั้งค่า Chat Model และ Embedding Model แล้วกด “สร้าง AI Index”</p>"
        )
        splitter.addWidget(self.chat_view)

        source_panel = QWidget()
        source_layout = QVBoxLayout(source_panel)
        source_layout.setContentsMargins(8, 0, 0, 0)
        source_layout.addWidget(QLabel("หลักฐานจากอีเมล"))
        self.source_list = QListWidget()
        self.source_list.itemActivated.connect(self._source_activated)
        source_layout.addWidget(self.source_list, 1)
        splitter.addWidget(source_panel)
        splitter.setSizes([850, 360])
        layout.addWidget(splitter, 1)

        ask_row = QHBoxLayout()
        self.question_input = QTextEdit()
        self.question_input.setPlaceholderText(
            "เช่น สรุปสิ่งที่ลูกค้า ABC ขอไว้ล่าสุด พร้อมวันที่และผู้ส่ง"
        )
        self.question_input.setMaximumHeight(100)
        ask_row.addWidget(self.question_input, 1)
        self.ask_button = QPushButton("ถาม AI")
        self.ask_button.setMinimumHeight(72)
        self.ask_button.clicked.connect(self.ask_ai)
        ask_row.addWidget(self.ask_button)
        layout.addLayout(ask_row)
        return page

    def _system_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)

        self.stats_label = QLabel("ยังไม่มีบัญชี Gmail")
        self.stats_label.setWordWrap(True)
        self.stats_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.stats_label)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separator)

        buttons = QHBoxLayout()
        settings_button = QPushButton("ตั้งค่า Gmail / Local AI")
        settings_button.clicked.connect(self.open_settings)
        buttons.addWidget(settings_button)
        data_button = QPushButton("เปิดโฟลเดอร์ข้อมูล")
        data_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.paths.data_dir)))
        )
        buttons.addWidget(data_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        privacy = QLabel(
            "ข้อมูลอีเมล, OAuth token และ raw MIME เก็บบนเครื่องนี้โดยตรง แอปใช้ Gmail readonly scope "
            "และไม่แก้ไขหรือลบเมลในบัญชี Google"
        )
        privacy.setWordWrap(True)
        privacy.setObjectName("muted")
        layout.addWidget(privacy)
        layout.addStretch(1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #0c111b; color: #e7edf5; }
            QLabel#title { font-size: 22px; font-weight: 700; color: #ff5f8f; }
            QLabel#status, QLabel#muted { color: #8b98a9; }
            QLineEdit, QTextEdit, QTextBrowser, QTableWidget, QListWidget, QComboBox {
                background: #121a28; border: 1px solid #27344a; border-radius: 7px;
                padding: 7px; selection-background-color: #325d88;
            }
            QPushButton {
                background: #1b2940; border: 1px solid #344764; border-radius: 7px;
                padding: 8px 12px; font-weight: 600;
            }
            QPushButton:hover { background: #253956; }
            QPushButton:disabled { color: #657083; background: #151c28; }
            QTabWidget::pane { border: 1px solid #27344a; border-radius: 8px; }
            QTabBar::tab { background: #141d2b; padding: 10px 18px; margin-right: 2px; }
            QTabBar::tab:selected { background: #243753; color: #ffffff; }
            QHeaderView::section { background: #172235; padding: 8px; border: 0; }
            QProgressBar { border: 1px solid #27344a; border-radius: 6px; text-align: center; }
            QProgressBar::chunk { background: #ff5f8f; border-radius: 5px; }
            QGroupBox { border: 1px solid #27344a; border-radius: 8px; margin-top: 10px; padding: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            """
        )

    def current_account_id(self) -> int | None:
        value = self.account_combo.currentData()
        return int(value) if value is not None else None

    def reload_accounts(self, select_id: int | None = None) -> None:
        current = select_id or self.current_account_id()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        accounts = self.database.list_accounts()
        for account in accounts:
            self.account_combo.addItem(account.email, account.id)
        if current is not None:
            index = self.account_combo.findData(current)
            if index >= 0:
                self.account_combo.setCurrentIndex(index)
        self.account_combo.blockSignals(False)
        enabled = bool(accounts)
        for button in (self.sync_button, self.full_sync_button, self.index_button, self.ask_button):
            button.setEnabled(enabled)
        self._account_changed()

    def _account_changed(self) -> None:
        account_id = self.current_account_id()
        if account_id is None:
            self.mail_table.setRowCount(0)
            self.mail_view.clear()
            self.stats_label.setText("ยังไม่มีบัญชี Gmail กด “เชื่อม Gmail” แล้วเลือก credentials.json")
            return
        self.search_mail()
        self.refresh_stats()
        self.chat_view.setHtml(
            "<h3>AI Memory</h3><p>ถามข้อมูลจาก Gmail archive ของบัญชีที่เลือกได้</p>"
        )
        self.source_list.clear()

    def connect_gmail(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "เลือก OAuth credentials.json",
            str(Path.home()),
            "Google OAuth JSON (*.json)",
        )
        if not filename:
            return

        def task(progress: Callable[[int, int, str], None], cancel: threading.Event) -> int:
            del cancel
            progress(0, 0, "กำลังเปิด Google OAuth ในเบราว์เซอร์")
            return self.sync_service.connect_account(Path(filename))

        self._run_task(
            task,
            on_result=lambda account_id: self._account_connected(int(account_id)),
            busy_text="กำลังเชื่อม Gmail",
        )

    def _account_connected(self, account_id: int) -> None:
        self.reload_accounts(select_id=account_id)
        self.start_sync(force_full=True)

    def start_sync(self, *, force_full: bool, silent: bool = False) -> None:
        account_id = self.current_account_id()
        if account_id is None or self.sync_running:
            return
        self.sync_running = True
        self._set_sync_buttons(False)

        def task(progress: Callable[[int, int, str], None], cancel: threading.Event) -> dict[str, Any]:
            result = self.sync_service.sync_account(
                account_id,
                force_full=force_full,
                progress=progress,
                cancel_event=cancel,
            )
            if self.settings.auto_index and self.settings.embedding_model:
                result["indexed"] = self.rag.index_pending(account_id, progress)
            return result

        def completed(result: dict[str, Any]) -> None:
            self.sync_running = False
            self._set_sync_buttons(True)
            self.search_mail()
            self.refresh_stats()
            failed = len(result.get("failed", {}))
            self.status_label.setText(
                f"sync {result['mode']}: ตรวจ {result['processed']:,}, "
                f"เปลี่ยน {result['changed']:,}, ลบจาก Gmail {result['deleted']:,}, "
                f"ผิดพลาด {failed:,}"
            )

        def failed(message: str, trace: str) -> None:
            self.sync_running = False
            self._set_sync_buttons(True)
            self._task_error(message, trace, silent=silent)

        self._run_task(
            task,
            on_result=completed,
            on_error=failed,
            busy_text="กำลัง sync Gmail",
        )

    def start_indexing(self) -> None:
        account_id = self.current_account_id()
        if account_id is None:
            return
        if not self.settings.embedding_model:
            self.open_settings()
            if not self.settings.embedding_model:
                return

        def task(progress: Callable[[int, int, str], None], cancel: threading.Event) -> int:
            del cancel
            return self.rag.index_pending(account_id, progress)

        self._run_task(
            task,
            on_result=lambda count: self._index_complete(int(count)),
            busy_text="กำลังสร้าง AI index",
        )

    def _index_complete(self, count: int) -> None:
        self.status_label.setText(f"สร้าง embedding เพิ่ม {count:,} chunks")
        self.refresh_stats()

    def search_mail(self) -> None:
        account_id = self.current_account_id()
        if account_id is None:
            return
        query = self.search_input.text().strip()
        rows = (
            self.database.search_messages(account_id, query)
            if query
            else self.database.recent_messages(account_id)
        )
        self.mail_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            date_item = QTableWidgetItem(self._format_date(int(row["internal_date"])))
            date_item.setData(Qt.ItemDataRole.UserRole, str(row["message_id"]))
            sender = QTableWidgetItem(str(row["sender"]))
            subject = QTableWidgetItem(str(row["subject"]))
            attachments = QTableWidgetItem("📎" if row["has_attachments"] else "")
            self.mail_table.setItem(row_index, 0, date_item)
            self.mail_table.setItem(row_index, 1, sender)
            self.mail_table.setItem(row_index, 2, subject)
            self.mail_table.setItem(row_index, 3, attachments)
        self.status_label.setText(f"พบ {len(rows):,} รายการ")
        if rows:
            self.mail_table.selectRow(0)
        else:
            self.mail_view.clear()
            self.export_button.setEnabled(False)

    @staticmethod
    def _format_date(epoch_ms: int) -> str:
        if not epoch_ms:
            return ""
        return datetime.fromtimestamp(epoch_ms / 1000).astimezone().strftime("%Y-%m-%d %H:%M")

    def _selected_message_id(self) -> str | None:
        rows = self.mail_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.mail_table.item(rows[0].row(), 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _mail_selected(self) -> None:
        account_id = self.current_account_id()
        message_id = self._selected_message_id()
        if account_id is None or not message_id:
            return
        message = self.database.get_message(account_id, message_id)
        labels = ", ".join(json.loads(message["labels_json"] or "[]"))
        attachment_html = ""
        if message["attachments"]:
            items = "".join(
                f"<li>{html.escape(str(item['filename']))} "
                f"({int(item['size']) / 1024:.1f} KB)</li>"
                for item in message["attachments"]
            )
            attachment_html = f"<h4>ไฟล์แนบ</h4><ul>{items}</ul>"
        body = html.escape(message["body_text"] or message["snippet"] or "(ไม่มีข้อความ)")
        self.mail_view.setHtml(
            f"<h2>{html.escape(message['subject'])}</h2>"
            f"<p><b>From:</b> {html.escape(message['sender'])}<br>"
            f"<b>To:</b> {html.escape(message['recipients'])}<br>"
            f"<b>Cc:</b> {html.escape(message['cc'])}<br>"
            f"<b>Date:</b> {self._format_date(int(message['internal_date']))}<br>"
            f"<b>Labels:</b> {html.escape(labels)}</p>"
            f"{attachment_html}<hr><pre style='white-space: pre-wrap'>{body}</pre>"
        )
        self.export_button.setEnabled(message["raw_mime"] is not None)

    def export_current_message(self) -> None:
        account_id = self.current_account_id()
        message_id = self._selected_message_id()
        if account_id is None or not message_id:
            return
        message = self.database.get_message(account_id, message_id)
        default_name = self._safe_export_name(message["subject"]) + ".eml"
        filename, _ = QFileDialog.getSaveFileName(
            self, "Export email", str(Path.home() / default_name), "Email (*.eml)"
        )
        if not filename:
            return
        try:
            Path(filename).write_bytes(self.database.get_raw_message(account_id, message_id))
        except Exception as exc:
            QMessageBox.critical(self, "Export ไม่สำเร็จ", str(exc))
        else:
            self.status_label.setText(f"บันทึก {filename}")

    @staticmethod
    def _safe_export_name(subject: str) -> str:
        cleaned = "".join(char if char.isalnum() or char in " -_" else "_" for char in subject)
        return cleaned.strip()[:80] or "email"

    def ask_ai(self) -> None:
        account_id = self.current_account_id()
        question = self.question_input.toPlainText().strip()
        if account_id is None or not question:
            return
        self.question_input.clear()
        self._append_chat("คุณ", question)

        session_id = self.chat_sessions.get(account_id)
        if session_id is None:
            session_id = self.database.create_chat_session(account_id, title=question[:80])
            self.chat_sessions[account_id] = session_id
        history = self.database.chat_history(session_id, limit=10)
        self.database.save_chat_message(session_id, "user", question)
        self.ask_button.setEnabled(False)

        def task(progress: Callable[[int, int, str], None], cancel: threading.Event) -> AIAnswer:
            del cancel
            progress(0, 0, "กำลังค้นหลักฐานและถามโมเดล")
            return self.rag.answer(account_id, question, history)

        def completed(answer: AIAnswer) -> None:
            self.ask_button.setEnabled(True)
            self.database.save_chat_message(session_id, "assistant", answer.answer)
            self._append_chat("Cherry", answer.answer)
            self._show_sources(answer.sources)

        def failed(message: str, trace: str) -> None:
            self.ask_button.setEnabled(True)
            self._task_error(message, trace)

        self._run_task(task, on_result=completed, on_error=failed, busy_text="AI กำลังค้นอีเมล")

    def _append_chat(self, speaker: str, text: str) -> None:
        self.chat_view.append(
            f"<p><b>{html.escape(speaker)}:</b><br>{html.escape(text).replace(chr(10), '<br>')}</p>"
        )
        self.chat_view.verticalScrollBar().setValue(self.chat_view.verticalScrollBar().maximum())

    def _show_sources(self, sources: list[RetrievedChunk]) -> None:
        self.current_sources = sources
        self.source_list.clear()
        for index, source in enumerate(sources, start=1):
            item = QListWidgetItem(
                f"[{index}] {self._format_date(source.internal_date)}\n"
                f"{source.subject}\n{source.sender}"
            )
            item.setData(Qt.ItemDataRole.UserRole, source.message_id)
            self.source_list.addItem(item)

    def _source_activated(self, item: QListWidgetItem) -> None:
        message_id = str(item.data(Qt.ItemDataRole.UserRole))
        for row in range(self.mail_table.rowCount()):
            table_item = self.mail_table.item(row, 0)
            if table_item and table_item.data(Qt.ItemDataRole.UserRole) == message_id:
                self.mail_table.selectRow(row)
                self.tabs.setCurrentIndex(0)
                return
        self.search_input.setText(f'"{message_id}"')
        account_id = self.current_account_id()
        if account_id is not None:
            try:
                message = self.database.get_message(account_id, message_id)
            except KeyError:
                return
            self.search_input.setText(f'subject:"{message["subject"]}"')
            self.search_mail()
            self.tabs.setCurrentIndex(0)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        self.settings = dialog.settings()
        self.settings_store.save(self.settings)
        self.sync_service.refresh_settings(self.settings)
        self.rag.refresh_settings(self.settings)
        self._restart_timer()
        self.status_label.setText("บันทึกการตั้งค่าแล้ว")

    def refresh_stats(self) -> None:
        account_id = self.current_account_id()
        if account_id is None:
            return
        account = self.database.get_account(account_id)
        stats = self.database.statistics(account_id)
        gib = stats["bytes"] / (1024**3)
        self.stats_label.setText(
            f"บัญชี: {account.email}\n"
            f"เมลที่ใช้งาน: {stats['active_messages']:,}\n"
            f"เมลที่ถูกลบจาก Gmail แต่ยังเก็บใน archive: {stats['archived_deleted']:,}\n"
            f"ขนาดตาม Gmail estimate: {gib:,.2f} GiB\n"
            f"RAG chunks: {stats['chunks']:,}\n"
            f"FTS tokenizer: {self.database.get_meta('fts_tokenizer', 'unknown')}\n"
            f"Sync ล่าสุด: {account.last_sync_at or '-'}\n"
            f"สถานะ: {account.sync_status}\n"
            f"Database: {self.paths.database_path}"
        )

    def _restart_timer(self) -> None:
        if not hasattr(self, "auto_sync_timer"):
            return
        self.auto_sync_timer.stop()
        if self.settings.auto_sync:
            self.auto_sync_timer.start(self.settings.sync_interval_minutes * 60 * 1000)

    def _auto_sync(self) -> None:
        if self.settings.auto_sync and not self.sync_running and self.current_account_id() is not None:
            self.start_sync(force_full=False, silent=True)

    def _set_sync_buttons(self, enabled: bool) -> None:
        has_account = self.current_account_id() is not None
        self.sync_button.setEnabled(enabled and has_account)
        self.full_sync_button.setEnabled(enabled and has_account)

    def _run_task(
        self,
        task: Callable[[Callable[[int, int, str], None], threading.Event], Any],
        *,
        on_result: Callable[[Any], None],
        busy_text: str,
        on_error: Callable[[str, str], None] | None = None,
    ) -> None:
        worker = TaskWorker(task)
        self.workers.add(worker)
        self.status_label.setText(busy_text)
        self.progress_bar.setRange(0, 0)
        worker.progress.connect(self._task_progress)
        worker.result.connect(on_result)
        worker.error.connect(on_error or self._task_error)

        def finished() -> None:
            self.workers.discard(worker)
            worker.deleteLater()
            if not self.workers:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)

        worker.finished.connect(finished)
        worker.start()

    def _task_progress(self, current: int, total: int, message: str) -> None:
        self.status_label.setText(message)
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(min(current, total))
        else:
            self.progress_bar.setRange(0, 0)

    def _task_error(self, message: str, trace: str, *, silent: bool = False) -> None:
        LOGGER.error("Background task failed: %s\n%s", message, trace)
        self.status_label.setText(f"ผิดพลาด: {message}")
        if not silent:
            QMessageBox.critical(self, "Cherry Mail Memory", message)

    def closeEvent(self, event: QCloseEvent) -> None:
        for worker in list(self.workers):
            worker.cancel()
        event.accept()
