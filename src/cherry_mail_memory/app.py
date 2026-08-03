from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from cherry_mail_memory import __version__
from cherry_mail_memory.config import APP_NAME, AppPaths, SettingsStore
from cherry_mail_memory.database import Database
from cherry_mail_memory.ui.main_window import MainWindow


def configure_logging(paths: AppPaths) -> None:
    handler = RotatingFileHandler(
        paths.log_dir / "cherry-mail-memory.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName(APP_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName("paddman")
    application.setFont(QFont("Segoe UI", 10))

    try:
        paths = AppPaths.create()
        configure_logging(paths)
        settings_store = SettingsStore(paths.config_path)
        settings = settings_store.load()
        database = Database(paths.database_path)
        window = MainWindow(database, settings_store, settings, paths)
        window.show()
        return application.exec()
    except Exception as exc:
        logging.exception("Unable to start Cherry Mail Memory")
        QMessageBox.critical(None, "Cherry Mail Memory", f"เปิดแอปไม่สำเร็จ:\n{exc}")
        return 1
