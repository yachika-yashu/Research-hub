import logging # Core Python logging library
import sys # System-specific parameters and functions
from logging.handlers import RotatingFileHandler # File handler that rotates logs when they reach a certain size
import os # Operating system dependent functionality

def setup_logging():
    """Sets up a production-grade logging system."""
    # Create logs directory if it doesn't exist
    os.makedirs("logs", exist_ok=True)

    log_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # 1. Console Handler Sends logs to terminal / console (for Docker logs/AWS CloudWatch) because there Logs are not stored in files logs are collected from stdout
    console_handler = logging.StreamHandler(sys.stdout) # centralized logging in prod environment
    console_handler.setFormatter(log_formatter) #Applies the format you defined

    # 2. File Handler (for local persistence) Writes logs to file
    file_handler = RotatingFileHandler(      #the handler rotates it by renaming the current log file and opening a new, empty file to continue logging. if file size exceeds maxBytes, oldest file in the sequence is deleted to make room for the new one.
        "logs/researchhub.log", maxBytes=10*1024*1024, backupCount=5
    ) # 10MB max file size, 5 backup files
    file_handler.setFormatter(log_formatter) #Applies the format you defined

    # Root Logger Configuration
    root_logger = logging.getLogger() # Get the root logger
    root_logger.setLevel(logging.INFO) # only logs>=info will be shown heirarchy is debug<info<warning<error<critical
    root_logger.addHandler(console_handler)  #logs will be shown on console
    root_logger.addHandler(file_handler) #logs will be written to file

    # Set levels for specific third-party loggers to reduce noise
    logging.getLogger("gunicorn").setLevel(logging.INFO) #gunicorn is web server
    logging.getLogger("gunicorn.error").setLevel(logging.INFO) #gunicorn error level
    logging.getLogger("gunicorn.access").setLevel(logging.WARNING) #gunicorn access level - http requests
    logging.getLogger("httpx").setLevel(logging.WARNING) #httpx is for http requests
    logging.getLogger("docling").setLevel(logging.INFO) #docling is for document processing

    logging.info("Logging system initialized (ResearchHub v1.1.2)")

logger = logging.getLogger("ResearchHub")
