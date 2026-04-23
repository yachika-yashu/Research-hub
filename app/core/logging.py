import logging
import sys
from logging.handlers import RotatingFileHandler
import os

def setup_logging():
    """Sets up a production-grade logging system."""
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)
    
    log_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    
    # 1. Console Handler (for Docker logs/AWS CloudWatch)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    
    # 2. File Handler (for local persistence)
    file_handler = RotatingFileHandler(
        "logs/hanuman.log", maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setFormatter(log_formatter)
    
    # Root Logger Configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # Set levels for specific third-party loggers to reduce noise
    logging.getLogger("uvicorn").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("docling").setLevel(logging.INFO)

    logging.info("Logging system initialized (Hanuman v1.1.2)")

logger = logging.getLogger("hanuman")
