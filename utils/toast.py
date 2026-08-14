"""Non-blocking slide-in notification, for status that shouldn't interrupt
the user the way a QMessageBox does — contrast with a confirmation dialog,
which is still the right tool when the user needs to make a choice."""
from PySide6.QtWidgets import QFrame, QLabel, QHBoxLayout, QWidget
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve

_STYLES = {
    "success": {"bg": "#1e3a2e", "border": "#30d158", "icon_color": "#30d158"},
    "warning": {"bg": "#3a2e1e", "border": "#ff9f0a", "icon_color": "#ff9f0a"},
}


def show_toast(parent: QWidget, message: str, icon: str = "✓",
                kind: str = "success", duration_ms: int = 2800):
    """Slide a small notification in from the bottom-right corner of
    *parent* and back out after duration_ms. Fire-and-forget — the toast
    keeps its own animation objects alive via attributes on itself until
    the slide-out finishes, then deletes itself."""
    style = _STYLES.get(kind, _STYLES["success"])

    toast = QFrame(parent)
    toast.setWindowFlags(Qt.FramelessWindowHint | Qt.ToolTip)
    toast.setAttribute(Qt.WA_TranslucentBackground, False)
    toast.setStyleSheet(f"""
        QFrame {{
            background: {style['bg']};
            border: 1px solid {style['border']};
            border-radius: 8px;
        }}
        QLabel {{ color: #e5e5ea; font-size: 13px; background: transparent; border: none; }}
    """)

    h = QHBoxLayout(toast)
    h.setContentsMargins(14, 10, 14, 10)
    h.setSpacing(8)
    icon_label = QLabel(icon)
    icon_label.setStyleSheet(
        f"color: {style['icon_color']}; font-size: 16px; font-weight: bold; "
        "background:transparent; border:none;"
    )
    text_label = QLabel(message)
    h.addWidget(icon_label)
    h.addWidget(text_label)

    toast.adjustSize()
    tw, th = toast.width(), toast.height()

    pw, ph = parent.width(), parent.height()
    margin = 16
    shown_x = pw - tw - margin
    hidden_x = pw + tw  # starts off-screen to the right
    y = ph - th - margin

    toast.setGeometry(hidden_x, y, tw, th)
    toast.show()
    toast.raise_()

    anim_in = QPropertyAnimation(toast, b"geometry")
    anim_in.setDuration(280)
    anim_in.setEasingCurve(QEasingCurve.OutCubic)
    anim_in.setStartValue(QRect(hidden_x, y, tw, th))
    anim_in.setEndValue(QRect(shown_x, y, tw, th))
    anim_in.start()
    toast._anim_in = anim_in  # keep a reference so GC doesn't kill it

    def _slide_out():
        anim_out = QPropertyAnimation(toast, b"geometry")
        anim_out.setDuration(280)
        anim_out.setEasingCurve(QEasingCurve.InCubic)
        anim_out.setStartValue(QRect(shown_x, y, tw, th))
        anim_out.setEndValue(QRect(hidden_x, y, tw, th))
        anim_out.finished.connect(toast.deleteLater)
        anim_out.start()
        toast._anim_out = anim_out

    QTimer.singleShot(duration_ms, _slide_out)
