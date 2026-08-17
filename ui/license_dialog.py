"""ui/license_dialog.py — view current edition, activate or deactivate a
Pro license key. Reachable from Help → License… (main.py).

activate()/deactivate() now call out to the licensing service (see
services/licensing_client.py), so they're no longer instant local calls —
run them on a worker thread (same shape as utils/entitlement_fetcher.py's
EntitlementConfigFetcher) so a slow/unreachable server never freezes the
dialog.
"""
from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.entitlements import Edition, entitlements
from services.license_manager import license_manager


class _LicenseActionWorker(QThread):
    """Runs a single license_manager call (activate or deactivate) off
    the UI thread and emits whatever it returns."""

    finished_with_result = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        self.finished_with_result.emit(self._fn())


class LicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("QForge License")
        self.setMinimumWidth(440)
        self._worker = None
        self._build_ui()

    def _build_ui(self):
        # Rebuilding the layout from scratch on every refresh (activate/
        # deactivate) is simpler and less error-prone than toggling the
        # visibility of two parallel sets of widgets.
        old_layout = self.layout()
        if old_layout is not None:
            QWidget().setLayout(old_layout)  # detach & discard old widgets

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.setLayout(layout)

        if entitlements.edition() is Edition.PRO:
            self._build_pro_view(layout)
        else:
            self._build_free_view(layout)

    def _build_pro_view(self, layout):
        layout.addWidget(QLabel("<b>QForge Pro</b> — thank you for your support."))

        payload = license_manager.load() or {}
        email = payload.get("email", "")
        license_id = payload.get("license_id", "")
        expires_at = payload.get("expires_at")

        if email:
            layout.addWidget(QLabel(f"Licensed to: {_mask_email(email)}"))
        if license_id:
            layout.addWidget(QLabel(f"License ID: {license_id[:8]}…"))
        layout.addWidget(QLabel(f"Expires: {expires_at or 'No expiration'}"))

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        layout.addWidget(divider)

        self.status_label = QLabel("")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.deactivate_btn = QPushButton("Deactivate License")
        self.deactivate_btn.clicked.connect(self._deactivate)
        layout.addWidget(self.deactivate_btn)

    def _build_free_view(self, layout):
        layout.addWidget(QLabel("<b>QForge Free</b>"))
        layout.addWidget(QLabel("Have a license key? Paste it below to activate Pro."))

        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Paste your license key here")
        layout.addWidget(self.key_input)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #ff453a;")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.status_label = QLabel("")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.activate_btn = QPushButton("Activate")
        self.activate_btn.setDefault(True)
        self.activate_btn.clicked.connect(self._activate)
        layout.addWidget(self.activate_btn)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        layout.addWidget(divider)

        upgrade_row = QHBoxLayout()
        upgrade_row.addWidget(QLabel(f"Don't have a license? {entitlements.price_label()}"))
        upgrade_row.addStretch(1)
        upgrade_btn = QPushButton("Upgrade to Pro →")
        upgrade_btn.clicked.connect(self._open_pricing_page)
        upgrade_row.addWidget(upgrade_btn)
        layout.addLayout(upgrade_row)

    def _set_busy(self, busy: bool, status_text: str = ""):
        self.status_label.setText(status_text)
        self.status_label.setVisible(busy)
        if hasattr(self, "activate_btn"):
            self.activate_btn.setEnabled(not busy)
            self.key_input.setEnabled(not busy)
        if hasattr(self, "deactivate_btn"):
            self.deactivate_btn.setEnabled(not busy)

    def _run_worker(self, fn, on_finished):
        self._worker = _LicenseActionWorker(fn, parent=self)
        self._worker.finished_with_result.connect(on_finished)
        self._worker.start()

    def _activate(self):
        key_string = self.key_input.text().strip()
        if not key_string:
            return
        self.error_label.hide()
        self._set_busy(True, "Activating…")
        self._run_worker(lambda: license_manager.activate(key_string), self._on_activate_finished)

    def _on_activate_finished(self, result):
        ok, reason = result
        self._set_busy(False)
        if not ok:
            self.error_label.setText(reason)
            self.error_label.show()
            return
        QMessageBox.information(self, "License Activated", "QForge Pro is now active.")
        self.accept()

    def _deactivate(self):
        reply = QMessageBox.question(
            self, "Deactivate License",
            "Remove this license and revert to QForge Free?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._set_busy(True, "Deactivating…")
        self._run_worker(license_manager.deactivate, self._on_deactivate_finished)

    def _on_deactivate_finished(self, _result):
        self._build_ui()

    def _open_pricing_page(self):
        QDesktopServices.openUrl(QUrl(entitlements.pricing_url()))


def _mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, _, domain = email.partition("@")
    if len(name) <= 2:
        masked = name[0] + "*" * max(len(name) - 1, 1)
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"
