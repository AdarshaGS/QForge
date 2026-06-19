"""
Credential Manager for secure storage of database passwords.
Uses platform-native keyring when available, falls back to encrypted file storage.
"""
import json
import os
import base64
from typing import Dict, Optional, Any
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    keyring = None

logger = logging.getLogger(__name__)

# Constants
SERVICE_NAME = "QForge"
CREDENTIALS_FILE = os.path.join(
    os.path.expanduser("~"),
    "Library", "Application Support", "QForge", "credentials.enc"
)
MASTER_KEY_FILE = os.path.join(
    os.path.expanduser("~"),
    "Library", "Application Support", "QForge", ".master_key"
)


def _ensure_qforge_dir():
    """Ensure QForge application support directory exists."""
    qforge_dir = os.path.dirname(CREDENTIALS_FILE)
    os.makedirs(qforge_dir, exist_ok=True)


def _get_or_create_master_key() -> bytes:
    """Get or create the master encryption key."""
    _ensure_qforge_dir()

    if os.path.exists(MASTER_KEY_FILE):
        with open(MASTER_KEY_FILE, 'rb') as f:
            return f.read()

    # Generate a new master key
    master_key = Fernet.generate_key()
    with open(MASTER_KEY_FILE, 'wb') as f:
        f.write(master_key)
    # Try to hide the file on Unix systems
    if hasattr(os, 'chmod'):
        try:
            os.chmod(MASTER_KEY_FILE, 0o600)  # Read/write only for owner
        except:
            pass

    return master_key


def _get_encryption_key() -> bytes:
    """Derive encryption key from master key."""
    master_key = _get_or_create_master_key()
    # Use a fixed salt for consistency (in practice, you might want to use a per-installation salt)
    salt = b'qforge_credential_salt_v1'
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(master_key))
    return key


def _encrypt_data(data: str) -> str:
    """Encrypt data using Fernet symmetric encryption."""
    f = Fernet(_get_encryption_key())
    encrypted = f.encrypt(data.encode())
    return base64.urlsafe_b64encode(encrypted).decode()


def _decrypt_data(encrypted_data: str) -> str:
    """Decrypt data using Fernet symmetric encryption."""
    f = Fernet(_get_encryption_key())
    decoded = base64.urlsafe_b64decode(encrypted_data.encode())
    decrypted = f.decrypt(decoded)
    return decrypted.decode()


class CredentialManager:
    """Manages secure storage and retrieval of database credentials."""

    def __init__(self):
        self._use_keyring = KEYRING_AVAILABLE
        if self._use_keyring:
            try:
                # Test if keyring is actually usable
                keyring.get_password(SERVICE_NAME, "test")
            except Exception:
                logger.warning("Keyring available but not functional, falling back to encrypted storage")
                self._use_keyring = False

    def store_password(self, connection_name: str, password: str) -> bool:
        """
        Store a password securely for a connection.

        Args:
            connection_name: Unique identifier for the connection
            password: The password to store

        Returns:
            bool: True if stored successfully
        """
        if not password:
            # Remove stored credential if password is empty
            return self.delete_password(connection_name)

        try:
            if self._use_keyring:
                keyring.set_password(SERVICE_NAME, connection_name, password)
                logger.debug(f"Stored password for '{connection_name}' in keyring")
                return True
            else:
                # Fallback to encrypted file storage
                self._ensure_qforge_dir()

                # Load existing credentials
                credentials = {}
                if os.path.exists(CREDENTIALS_FILE):
                    try:
                        with open(CREDENTIALS_FILE, 'r') as f:
                            credentials = json.load(f)
                    except (json.JSONDecodeError, IOError):
                        logger.warning("Could not read existing credentials file, starting fresh")
                        credentials = {}

                # Store encrypted password
                credentials[connection_name] = _encrypt_data(password)

                # Save back to file
                with open(CREDENTIALS_FILE, 'w') as f:
                    json.dump(credentials, f, indent=2)

                # Try to restrict file permissions
                if hasattr(os, 'chmod'):
                    try:
                        os.chmod(CREDENTIALS_FILE, 0o600)
                    except:
                        pass

                logger.debug(f"Stored password for '{connection_name}' in encrypted file")
                return True

        except Exception as e:
            logger.error(f"Failed to store password for '{connection_name}': {e}")
            return False

    def get_password(self, connection_name: str) -> Optional[str]:
        """
        Retrieve a password for a connection.

        Args:
            connection_name: Unique identifier for the connection

        Returns:
            str or None: The password if found, None otherwise
        """
        try:
            if self._use_keyring:
                password = keyring.get_password(SERVICE_NAME, connection_name)
                if password is not None:
                    logger.debug(f"Retrieved password for '{connection_name}' from keyring")
                return password
            else:
                # Fallback to encrypted file storage
                if not os.path.exists(CREDENTIALS_FILE):
                    return None

                with open(CREDENTIALS_FILE, 'r') as f:
                    credentials = json.load(f)

                encrypted_password = credentials.get(connection_name)
                if encrypted_password is None:
                    return None

                password = _decrypt_data(encrypted_password)
                logger.debug(f"Retrieved password for '{connection_name}' from encrypted file")
                return password

        except Exception as e:
            logger.error(f"Failed to retrieve password for '{connection_name}': {e}")
            return None

    def delete_password(self, connection_name: str) -> bool:
        """
        Delete a stored password for a connection.

        Args:
            connection_name: Unique identifier for the connection

        Returns:
            bool: True if deleted successfully (or if not found)
        """
        try:
            if self._use_keyring:
                keyring.delete_password(SERVICE_NAME, connection_name)
                logger.debug(f"Deleted password for '{connection_name}' from keyring")
                return True
            else:
                # Fallback to encrypted file storage
                if not os.path.exists(CREDENTIALS_FILE):
                    return True  # Already doesn't exist

                with open(CREDENTIALS_FILE, 'r') as f:
                    credentials = json.load(f)

                if connection_name in credentials:
                    del credentials[connection_name]
                    with open(CREDENTIALS_FILE, 'w') as f:
                        json.dump(credentials, f, indent=2)
                    logger.debug(f"Deleted password for '{connection_name}' from encrypted file")
                return True

        except Exception as e:
            logger.error(f"Failed to delete password for '{connection_name}': {e}")
            return False

    def list_stored_connections(self) -> list:
        """
        List all connection names that have stored credentials.

        Returns:
            list: List of connection names
        """
        try:
            if self._use_keyring:
                # Keyring doesn't have a good way to list all items for a service
                # This is a limitation - we'd need to track separately or use a different approach
                logger.warning("Keyring backend doesn't support listing stored connections")
                return []
            else:
                if not os.path.exists(CREDENTIALS_FILE):
                    return []

                with open(CREDENTIALS_FILE, 'r') as f:
                    credentials = json.load(f)
                return list(credentials.keys())

        except Exception as e:
            logger.error(f"Failed to list stored connections: {e}")
            return []


# Global instance
credential_manager = CredentialManager()


def store_connection_password(connection_name: str, password: str) -> bool:
    """Convenience function to store a connection password."""
    return credential_manager.store_password(connection_name, password)


def get_connection_password(connection_name: str) -> Optional[str]:
    """Convenience function to retrieve a connection password."""
    return credential_manager.get_password(connection_name)


def delete_connection_password(connection_name: str) -> bool:
    """Convenience function to delete a connection password."""
    return credential_manager.delete_password(connection_name)