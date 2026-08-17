"""ui/upgrade_dialog.py — the Qt-side counterpart to services/entitlements.py.

require_pro() and require_under_limit() are the single choke points every
Pro-gated action in the app calls through (issue #82's explicit ask: no
scattered `if isPremium: ...` checks anywhere else). services/entitlements.py
itself stays UI-free; this module owns "what the user sees when a gate is
hit" (issue #86).
"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from services.entitlements import Feature, Limit, entitlements


class UpgradeDialog(QDialog):
    """Shown when a Free user hits a Pro-only feature or limit.

    Deliberately two visually distinct zones: the top is *specific* to
    whatever just triggered this (a headline, an optional bold usage
    count, and a line or two of context) — the bottom is a fixed "Pro
    plan" card that looks the same every time, so a user skimming past
    several of these over a session learns to recognize it at a glance
    instead of re-reading it.
    """

    def __init__(self, headline: str, detail: str = "", usage_line: str = "", cta_line: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Upgrade to QForge Pro")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # ── Trigger section: what specifically brought the user here ────────
        title = QLabel(f"<b>{headline}</b>")
        title.setStyleSheet("font-size: 15px;")
        layout.addWidget(title)

        if usage_line:
            usage = QLabel(f"<b>{usage_line}</b>")
            usage.setStyleSheet("font-size: 22px; color: #ff9f0a;")
            layout.addWidget(usage)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            layout.addWidget(detail_label)

        if cta_line:
            cta_label = QLabel(cta_line)
            cta_label.setWordWrap(True)
            cta_label.setStyleSheet("color: #4F8CFF; font-weight: 600;")
            layout.addWidget(cta_label)

        # ── Pro plan card: the same every time, visually set apart from
        # the trigger-specific text above ────────────────────────────────
        card = QFrame()
        card.setObjectName("proPlanCard")
        card.setStyleSheet(
            "QFrame#proPlanCard {"
            " background: rgba(79, 140, 255, 0.10);"
            " border: 1px solid rgba(79, 140, 255, 0.35);"
            " border-radius: 8px;"
            "}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(8)

        plan_title = QLabel(f"<b>QForge Pro — {entitlements.price_label()}</b>")
        plan_title.setStyleSheet("font-size: 14px;")
        card_layout.addWidget(plan_title)

        for benefit in entitlements.pro_benefits():
            card_layout.addWidget(QLabel(f"✓  {benefit}"))

        upgrade_btn = QPushButton("Upgrade to Pro →")
        upgrade_btn.setDefault(True)
        upgrade_btn.clicked.connect(self._open_pricing_page)
        card_layout.addWidget(upgrade_btn)

        layout.addWidget(card)

        # ── De-emphasized secondary actions ──────────────────────────────
        bottom_row = QHBoxLayout()

        license_link = QPushButton("Already have a license? Enter License Key")
        license_link.setFlat(True)
        license_link.setCursor(Qt.PointingHandCursor)
        license_link.setStyleSheet(
            "QPushButton { color: #4F8CFF; border: none; background: transparent;"
            " text-align: left; padding: 0; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        license_link.clicked.connect(self._open_license_dialog)
        bottom_row.addWidget(license_link)

        bottom_row.addStretch(1)

        cancel_btn = QPushButton("Not now")
        cancel_btn.setFlat(True)
        cancel_btn.clicked.connect(self.reject)
        bottom_row.addWidget(cancel_btn)

        layout.addLayout(bottom_row)

    def _open_pricing_page(self):
        QDesktopServices.openUrl(QUrl(entitlements.pricing_url()))

    def _open_license_dialog(self):
        from ui.license_dialog import LicenseDialog
        dlg = LicenseDialog(parent=self.parent())
        if dlg.exec():
            self.accept()


def require_pro(feature: Feature, feature_label: str, parent, detail: str = "") -> bool:
    """True if *feature* is available under the current edition. If not,
    shows UpgradeDialog and returns False — callers should abort the
    gated action on a False return, e.g.:

        if not require_pro(Feature.SCHEMA_COMPARE, "Schema Compare", self):
            return
    """
    if entitlements.is_enabled(feature):
        return True
    UpgradeDialog(f"{feature_label} is a Pro feature", detail=detail, parent=parent).exec()
    return entitlements.is_enabled(feature)  # re-check: user may have just activated a key


def require_under_limit(limit: Limit, current_count: int, thing_label: str, parent, detail: str = "") -> bool:
    """True if adding one more (current_count + 1) stays within the current
    edition's limit. If not, shows UpgradeDialog and returns False — same
    calling convention as require_pro(), for count-based gates (max
    connections, max query tabs, max saved queries, ...) rather than
    boolean feature gates.

    *thing_label* is a plain lowercase plural noun phrase — "connections",
    "query tabs", "saved queries" — used to build the usage line and the
    default detail/CTA text.
    """
    cap = entitlements.limit(limit)
    if cap is None or current_count < cap:
        return True
    UpgradeDialog(
        "You've reached the Free plan limit",
        detail=detail or f"Free accounts can have up to {cap} {thing_label}.",
        usage_line=f"{current_count} / {cap} {thing_label} used",
        cta_line=f"Upgrade to Pro for unlimited {thing_label}.",
        parent=parent,
    ).exec()
    cap = entitlements.limit(limit)  # re-check: user may have just activated a key
    return cap is None or current_count < cap
