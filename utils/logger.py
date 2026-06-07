import logging
import os
from datetime import datetime
from pathlib import Path


def setup_logger(name="sql_workbench", level=logging.INFO):
    """
    Setup and configure logger for the application
    
    Args:
        name: Logger name
        level: Logging level (INFO, DEBUG, WARNING, ERROR)
    
    Returns:
        Configured logger instance
    """
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Create logs directory in user's home folder (writable location)
    log_dir = Path.home() / ".qforge" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log filename with date
    log_filename = log_dir / f"app_{datetime.now().strftime('%Y%m%d')}.log"
    
    # File handler - logs everything
    file_handler = logging.FileHandler(log_filename)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler - logs INFO and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger


def get_logger(name="sql_workbench"):
    """Get existing logger or create new one"""
    return logging.getLogger(name)
