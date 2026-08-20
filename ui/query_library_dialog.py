from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QLabel,
    QTextEdit,
    QLineEdit,
    QMessageBox,
    QInputDialog,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence


class QueryLibraryDialog(QDialog):
    """Browse, search, and manage every saved query (issue #130) — the
    inline Queries sidebar panel only shows a handful of each section;
    this is the full list reachable via "View all saved queries…"."""

    def __init__(self, saved_queries, parent=None):
        super().__init__(parent)

        self.saved_queries = saved_queries
        self.selected_query = None
        self.selected_name = None

        self.setWindowTitle("Saved Queries")
        self.resize(800, 600)

        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)

        self.init_ui()
        self.load_list()

    def init_ui(self):
        layout = QVBoxLayout()

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search saved queries...")
        self.search_input.textChanged.connect(self.filter_list)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(lambda _: self.use_selected_query())
        layout.addWidget(self.list_widget)

        preview_label = QLabel("Query Preview:")
        layout.addWidget(preview_label)

        self.query_preview = QTextEdit()
        self.query_preview.setReadOnly(True)
        self.query_preview.setMaximumHeight(150)
        layout.addWidget(self.query_preview)

        button_layout = QHBoxLayout()

        self.favorite_btn = QPushButton("☆ Favorite")
        self.favorite_btn.clicked.connect(self.toggle_favorite)
        button_layout.addWidget(self.favorite_btn)

        self.rename_btn = QPushButton("Rename")
        self.rename_btn.clicked.connect(self.rename_selected)
        button_layout.addWidget(self.rename_btn)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_selected)
        button_layout.addWidget(self.delete_btn)

        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        self.use_btn = QPushButton("Use Query")
        self.use_btn.clicked.connect(self.use_selected_query)
        button_layout.addWidget(self.use_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

    def load_list(self, entries=None):
        self.list_widget.clear()

        if entries is None:
            entries = self.saved_queries.queries

        for entry in entries:
            star = "★" if entry.get("favorite") else "☆"
            preview = entry["query"].replace("\n", " ")[:80]
            if len(entry["query"]) > 80:
                preview += "..."
            display_text = f"{star} {entry['name']}\n{preview}"

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, entry)
            self.list_widget.addItem(item)

        if entries:
            self.list_widget.setCurrentRow(0)

    def filter_list(self):
        search_text = self.search_input.text().strip()

        if not search_text:
            self.load_list()
            return

        self.load_list(self.saved_queries.search(search_text))

    def on_selection_changed(self, current, previous):
        if current is None:
            self.query_preview.clear()
            return

        entry = current.data(Qt.UserRole)
        self.query_preview.setPlainText(entry["query"])
        self.favorite_btn.setText("★ Unfavorite" if entry.get("favorite") else "☆ Favorite")

    def _current_entry(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def toggle_favorite(self):
        entry = self._current_entry()
        if entry is None:
            return
        self.saved_queries.toggle_favorite(entry["id"])
        self.filter_list()

    def rename_selected(self):
        entry = self._current_entry()
        if entry is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Query", "Name:", text=entry["name"])
        if ok and name.strip():
            self.saved_queries.update(entry["id"], name=name.strip())
            self.filter_list()

    def delete_selected(self):
        entry = self._current_entry()
        if entry is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Query",
            f"Delete '{entry['name']}'?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.saved_queries.delete(entry["id"])
            self.filter_list()

    def use_selected_query(self):
        entry = self._current_entry()

        if entry is None:
            QMessageBox.warning(self, "Warning", "Please select a query")
            return

        self.selected_query = entry["query"]
        self.selected_name = entry["name"]
        self.accept()

    def get_selected_query(self):
        return self.selected_query

    def get_selected_name(self):
        return self.selected_name
