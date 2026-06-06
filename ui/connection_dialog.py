import json
import os

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QWidget,
    QComboBox,
    QLabel,
    QCheckBox,
    QFileDialog
)
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtCore import Qt


class ConnectionDialog(QDialog):

    CONNECTION_FILE = "connections.json"
    LAST_CONNECTION_FILE = "last_connection.json"

    def __init__(self, auto_connect_last=False):
        super().__init__()

        self.selected_connection = None
        self.auto_connect_last = auto_connect_last

        self.setWindowTitle("Connection Manager")
        self.resize(850, 670)
        self.setMinimumSize(800, 450)
        self.setSizeGripEnabled(True)
        
        # Add Cmd+W shortcut to close dialog
        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.reject)

        self.init_ui()
        self.load_connections()
        
        # Auto-select last connection if enabled
        if auto_connect_last:
            self.select_last_connection()

    def init_ui(self):

        layout = QHBoxLayout()

        # ---------------------
        # LEFT PANEL
        # ---------------------
        
        left_panel = QWidget()
        left_layout = QVBoxLayout()
        
        # Search box for connections
        search_label = QLabel("🔍 Search Connections:")
        search_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        self.connection_search = QLineEdit()
        self.connection_search.setPlaceholderText("Type to filter connections...")
        self.connection_search.setStyleSheet("font-size: 12px; padding: 8px;")
        self.connection_search.textChanged.connect(self.filter_connections)
        
        left_layout.addWidget(search_label)
        left_layout.addWidget(self.connection_search)

        self.connection_list = QListWidget()
        left_layout.addWidget(self.connection_list)
        
        left_panel.setLayout(left_layout)
        layout.addWidget(left_panel)

        # ---------------------
        # RIGHT PANEL
        # ---------------------

        right_panel = QWidget()

        form_layout = QFormLayout()

        self.type_input = QComboBox()
        self.type_input.addItems(["MySQL", "PostgreSQL", "SQLite"])
        self.type_input.currentTextChanged.connect(self.on_type_changed)
        
        self.name_input = QLineEdit()
        self.host_input = QLineEdit()
        self.port_input = QLineEdit("3306")
        self.database_input = QLineEdit()
        self.user_input = QLineEdit()
        
        # Password field with visibility toggle
        password_widget = QWidget()
        password_layout = QHBoxLayout(password_widget)
        password_layout.setContentsMargins(0, 0, 0, 0)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_visible_btn = QPushButton("👁️")
        self.password_visible_btn.setMaximumWidth(30)
        self.password_visible_btn.clicked.connect(self.toggle_password_visibility)
        password_layout.addWidget(self.password_input)
        password_layout.addWidget(self.password_visible_btn)

        form_layout.addRow("Type", self.type_input)
        form_layout.addRow("Name", self.name_input)
        self.host_row = form_layout.addRow("Host", self.host_input)
        self.port_row = form_layout.addRow("Port", self.port_input)
        form_layout.addRow("Database/Path", self.database_input)
        self.user_row = form_layout.addRow("User", self.user_input)
        self.password_row = form_layout.addRow("Password", password_widget)
        
        # SSH Tunnel section
        form_layout.addRow("", QLabel(""))  # Spacer
        ssh_label = QLabel("🔒 SSH Tunnel (Optional):")
        ssh_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        form_layout.addRow("", ssh_label)
        
        self.ssh_enabled = QComboBox()
        self.ssh_enabled.addItems(["No", "Yes"])
        self.ssh_enabled.currentTextChanged.connect(self.on_ssh_enabled_changed)
        form_layout.addRow("Use SSH Tunnel", self.ssh_enabled)
        
        self.ssh_host_input = QLineEdit()
        self.ssh_port_input = QLineEdit("22")
        self.ssh_user_input = QLineEdit()
        
        # SSH Password with visibility toggle
        ssh_password_widget = QWidget()
        ssh_password_layout = QHBoxLayout(ssh_password_widget)
        ssh_password_layout.setContentsMargins(0, 0, 0, 0)
        self.ssh_password_input = QLineEdit()
        self.ssh_password_input.setEchoMode(QLineEdit.Password)
        self.ssh_password_visible_btn = QPushButton("👁️")
        self.ssh_password_visible_btn.setMaximumWidth(30)
        self.ssh_password_visible_btn.clicked.connect(self.toggle_ssh_password_visibility)
        ssh_password_layout.addWidget(self.ssh_password_input)
        ssh_password_layout.addWidget(self.ssh_password_visible_btn)
        
        # SSH Key checkbox and path
        self.ssh_use_key_checkbox = QCheckBox("Use SSH Key")
        self.ssh_use_key_checkbox.stateChanged.connect(self.on_ssh_key_checkbox_changed)
        # SSH Key Path with browse button
        self.ssh_key_path_input = QLineEdit()
        self.ssh_key_path_input.setPlaceholderText("Path to private key file (e.g., ~/.ssh/id_rsa)")
        self.ssh_key_path_input.setEnabled(False)
        
        self.ssh_key_browse_btn = QPushButton("📁")
        self.ssh_key_browse_btn.setFixedWidth(40)
        self.ssh_key_browse_btn.setEnabled(False)
        self.ssh_key_browse_btn.clicked.connect(self.browse_ssh_key)
        self.ssh_key_browse_btn.setToolTip("Browse for SSH key file")
        
        ssh_key_layout = QHBoxLayout()
        ssh_key_layout.addWidget(self.ssh_key_path_input)
        ssh_key_layout.addWidget(self.ssh_key_browse_btn)
        ssh_key_layout.setContentsMargins(0, 0, 0, 0)
        
        ssh_key_widget = QWidget()
        ssh_key_widget.setLayout(ssh_key_layout)
        
        self.ssh_host_row = form_layout.addRow("SSH Host", self.ssh_host_input)
        self.ssh_port_row = form_layout.addRow("SSH Port", self.ssh_port_input)
        self.ssh_user_row = form_layout.addRow("SSH User", self.ssh_user_input)
        self.ssh_password_row = form_layout.addRow("SSH Password", ssh_password_widget)
        form_layout.addRow("", self.ssh_use_key_checkbox)
        self.ssh_key_row = form_layout.addRow("SSH Key Path", ssh_key_widget)
        
        # Initially disable SSH fields
        self.set_ssh_fields_enabled(False)
        
        # Connection test status label
        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        self.test_status_label.setStyleSheet("padding: 5px; border-radius: 3px;")
        form_layout.addRow("", self.test_status_label)

        btn_layout = QHBoxLayout()

        self.add_btn = QPushButton("Add")
        self.update_btn = QPushButton("Update")
        self.delete_btn = QPushButton("Delete")
        self.test_btn = QPushButton("🔧 Test")
        self.connect_btn = QPushButton("✓ Connect")

        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.update_btn)
        btn_layout.addWidget(self.delete_btn)
        btn_layout.addWidget(self.test_btn)
        btn_layout.addWidget(self.connect_btn)

        wrapper = QVBoxLayout()

        wrapper.addLayout(form_layout)
        wrapper.addLayout(btn_layout)

        right_panel.setLayout(wrapper)

        layout.addWidget(right_panel)

        self.setLayout(layout)

        # events

        self.add_btn.clicked.connect(
            self.add_connection
        )

        self.update_btn.clicked.connect(
            self.update_connection
        )

        self.delete_btn.clicked.connect(
            self.delete_connection
        )
        
        self.test_btn.clicked.connect(
            self.test_connection
        )

        self.connect_btn.clicked.connect(
            self.connect_selected
        )

        self.connection_list.itemClicked.connect(
            self.load_selected_connection
        )
    
    def on_type_changed(self, db_type):
        """Handle database type change"""
        if db_type == "SQLite":
            # Hide unnecessary fields for SQLite
            self.host_input.setEnabled(False)
            self.port_input.setEnabled(False)
            self.user_input.setEnabled(False)
            self.password_input.setEnabled(False)
            self.database_input.setPlaceholderText("Path to .db file")
            # Disable SSH for SQLite
            self.ssh_enabled.setEnabled(False)
        else:
            # Show all fields for MySQL/PostgreSQL
            self.host_input.setEnabled(True)
            self.port_input.setEnabled(True)
            self.user_input.setEnabled(True)
            self.password_input.setEnabled(True)
            self.database_input.setPlaceholderText("Database name (optional)")
            self.ssh_enabled.setEnabled(True)
            
            # Set default port
            if db_type == "MySQL":
                self.port_input.setText("3306")
            elif db_type == "PostgreSQL":
                self.port_input.setText("5432")
    
    def on_ssh_enabled_changed(self, value):
        """Handle SSH tunnel enable/disable"""
        enabled = (value == "Yes")
        self.set_ssh_fields_enabled(enabled)
    
    def set_ssh_fields_enabled(self, enabled):
        """Enable or disable SSH fields"""
        self.ssh_host_input.setEnabled(enabled)
        self.ssh_port_input.setEnabled(enabled)
        self.ssh_user_input.setEnabled(enabled)
        self.ssh_password_input.setEnabled(enabled)
        self.ssh_use_key_checkbox.setEnabled(enabled)
        
        # Enable key path and browse button if using key
        if enabled and self.ssh_use_key_checkbox.isChecked():
            self.ssh_key_path_input.setEnabled(True)
            self.ssh_key_browse_btn.setEnabled(True)
        else:
            self.ssh_key_path_input.setEnabled(False)
            self.ssh_key_browse_btn.setEnabled(False)
    
    def browse_ssh_key(self):
        """Open file dialog to browse for SSH key"""
        home_dir = os.path.expanduser("~/.ssh")
        if not os.path.exists(home_dir):
            home_dir = os.path.expanduser("~")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            home_dir,
            "All Files (*)"
        )
        
        if file_path:
            self.ssh_key_path_input.setText(file_path)
    
    def on_ssh_key_checkbox_changed(self, state):
        """Handle SSH key checkbox state change"""
        use_key = (state == 2)  # Qt.Checked
        self.ssh_password_input.setEnabled(not use_key)
        self.ssh_password_visible_btn.setEnabled(not use_key)
        self.ssh_key_path_input.setEnabled(use_key)
        self.ssh_key_browse_btn.setEnabled(use_key)
    
    def toggle_password_visibility(self):
        """Toggle password visibility"""
        if self.password_input.echoMode() == QLineEdit.Password:
            self.password_input.setEchoMode(QLineEdit.Normal)
            self.password_visible_btn.setText("🚫")
        else:
            self.password_input.setEchoMode(QLineEdit.Password)
            self.password_visible_btn.setText("👁️")
    
    def toggle_ssh_password_visibility(self):
        """Toggle SSH password visibility"""
        if self.ssh_password_input.echoMode() == QLineEdit.Password:
            self.ssh_password_input.setEchoMode(QLineEdit.Normal)
            self.ssh_password_visible_btn.setText("🚫")
        else:
            self.ssh_password_input.setEchoMode(QLineEdit.Password)
            self.ssh_password_visible_btn.setText("👁️")

    def load_connections(self):

        self.connection_list.clear()

        self.connections = []

        if not os.path.exists(
            self.CONNECTION_FILE
        ):
            return

        try:

            with open(
                self.CONNECTION_FILE,
                "r"
            ) as file:

                self.connections = json.load(file)

            for connection in self.connections:

                self.connection_list.addItem(
                    connection["name"]
                )

        except Exception as ex:

            QMessageBox.critical(
                self,
                "Error",
                str(ex)
            )

    def save_connections(self):

        with open(
            self.CONNECTION_FILE,
            "w"
        ) as file:

            json.dump(
                self.connections,
                file,
                indent=4
            )

    def get_form_data(self):

        db_type = self.type_input.currentText().lower()
        
        data = {
            "type": db_type,
            "name": self.name_input.text(),
            "database": self.database_input.text()
        }
        
        # Add host/port/user/password only for non-SQLite
        if db_type != "sqlite":
            data["host"] = self.host_input.text()
            data["port"] = int(self.port_input.text())
            data["user"] = self.user_input.text()
            data["password"] = self.password_input.text()
            
            # Add SSH tunnel data if enabled
            if self.ssh_enabled.currentText() == "Yes":
                data["ssh_tunnel"] = {
                    "enabled": True,
                    "host": self.ssh_host_input.text(),
                    "port": int(self.ssh_port_input.text()),
                    "user": self.ssh_user_input.text(),
                    "use_key": self.ssh_use_key_checkbox.isChecked(),
                    "password": self.ssh_password_input.text() if not self.ssh_use_key_checkbox.isChecked() else "",
                    "key_path": self.ssh_key_path_input.text() if self.ssh_use_key_checkbox.isChecked() else ""
                }
            else:
                data["ssh_tunnel"] = {"enabled": False}
        
        return data

    def clear_form(self):

        self.type_input.setCurrentIndex(0)
        self.name_input.clear()
        self.host_input.clear()
        self.port_input.setText("3306")
        self.database_input.clear()
        self.user_input.clear()
        self.password_input.clear()
        
        # Clear SSH tunnel fields
        self.ssh_enabled.setCurrentIndex(0)
        self.ssh_host_input.clear()
        self.ssh_port_input.setText("22")
        self.ssh_user_input.clear()
        self.ssh_password_input.clear()
        self.ssh_use_key_checkbox.setChecked(False)
        self.ssh_key_path_input.clear()

    def add_connection(self):

        data = self.get_form_data()

        self.connections.append(data)

        self.save_connections()

        self.load_connections()

        self.clear_form()

    def update_connection(self):

        row = self.connection_list.currentRow()

        if row < 0:
            return

        self.connections[row] = (
            self.get_form_data()
        )

        self.save_connections()

        self.load_connections()

    def delete_connection(self):

        row = self.connection_list.currentRow()

        if row < 0:
            return

        del self.connections[row]

        self.save_connections()

        self.load_connections()

        self.clear_form()

    def load_selected_connection(self):

        row = self.connection_list.currentRow()

        if row < 0:
            return

        connection = self.connections[row]

        # Set type
        db_type = connection.get("type", "mysql")
        type_map = {"mysql": "MySQL", "postgresql": "PostgreSQL", "sqlite": "SQLite"}
        self.type_input.setCurrentText(type_map.get(db_type, "MySQL"))
        
        self.name_input.setText(
            connection["name"]
        )

        self.database_input.setText(
            connection["database"]
        )
        
        # Only set these for non-SQLite
        if db_type != "sqlite":
            self.host_input.setText(
                connection.get("host", "")
            )
            self.port_input.setText(
                str(connection.get("port", 3306))
            )
            self.user_input.setText(
                connection.get("user", "")
            )
            self.password_input.setText(
                connection.get("password", "")
            )            
            # Load SSH tunnel settings
            ssh_data = connection.get("ssh_tunnel", {"enabled": False})
            if ssh_data.get("enabled", False):
                self.ssh_enabled.setCurrentText("Yes")
                self.ssh_host_input.setText(ssh_data.get("host", ""))
                self.ssh_port_input.setText(str(ssh_data.get("port", 22)))
                self.ssh_user_input.setText(ssh_data.get("user", ""))
                
                # Check if using key or password
                use_key = ssh_data.get("use_key", False)
                self.ssh_use_key_checkbox.setChecked(use_key)
                
                if use_key:
                    self.ssh_key_path_input.setText(ssh_data.get("key_path", ""))
                else:
                    self.ssh_password_input.setText(ssh_data.get("password", ""))
            else:
                self.ssh_enabled.setCurrentText("No")
    def connect_selected(self):

        row = self.connection_list.currentRow()

        if row < 0:

            QMessageBox.warning(
                self,
                "Warning",
                "Select a connection"
            )

            return

        self.selected_connection = (
            self.connections[row]
        )
        
        # Save as last connection
        self.save_last_connection(self.selected_connection)

        self.accept()

    def get_selected_connection(self):

        return self.selected_connection
    
    def test_connection(self):
        """Test the connection without saving"""
        from services.db_service import DbService
        
        data = self.get_form_data()
        
        # Only name is required, database is optional
        if not data["name"]:
            self.test_status_label.setText("⚠️ Connection name is required")
            self.test_status_label.setStyleSheet("color: orange; padding: 5px; border-radius: 3px;")
            return
            self.test_status_label.setText("⚠️ Please fill in all required fields")
            self.test_status_label.setStyleSheet("color: orange; background: #3a3a3a; padding: 5px; border-radius: 3px;")
            return
        
        # Show testing message
        self.test_status_label.setText("⏳ Testing connection...")
        self.test_status_label.setStyleSheet("color: #0096FF; background: #2a2a2a; padding: 5px; border-radius: 3px;")
        self.test_btn.setEnabled(False)
        
        # Process events to show the message
        from PySide6.QtCore import QCoreApplication
        QCoreApplication.processEvents()
        
        db_service = DbService()
        
        try:
            db_service.connect(data)
            db_service.disconnect()
            self.test_status_label.setText(f"✓ Connection successful to '{data['name']}'")
            self.test_status_label.setStyleSheet("color: #00CC00; background: #1a3a1a; padding: 5px; border-radius: 3px;")
        except Exception as ex:
            self.test_status_label.setText(f"✗ Connection failed: {str(ex)}")
            self.test_status_label.setStyleSheet("color: #FF4444; background: #3a1a1a; padding: 5px; border-radius: 3px;")
        finally:
            self.test_btn.setEnabled(True)
    
    def filter_connections(self, search_text):
        """Filter connections based on search text"""
        search_text = search_text.lower().strip()
        
        for i in range(self.connection_list.count()):
            item = self.connection_list.item(i)
            if not search_text:
                item.setHidden(False)
            else:
                item.setHidden(search_text not in item.text().lower())
    
    def save_last_connection(self, connection):
        """Save the last connected database"""
        try:
            with open(self.LAST_CONNECTION_FILE, "w") as f:
                json.dump({"name": connection["name"]}, f)
        except Exception:
            pass  # Silently fail
    
    def select_last_connection(self):
        """Auto-select the last connected database"""
        try:
            if os.path.exists(self.LAST_CONNECTION_FILE):
                with open(self.LAST_CONNECTION_FILE, "r") as f:
                    last_conn = json.load(f)
                    last_name = last_conn.get("name")
                    
                    # Find and select the connection
                    for i in range(self.connection_list.count()):
                        if self.connection_list.item(i).text() == last_name:
                            self.connection_list.setCurrentRow(i)
                            self.load_selected_connection()
                            break
        except Exception:
            pass  # Silently fail