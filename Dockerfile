# Production-grade Dockerfile for ResearchHub Intelligence Platform

# BASE IMAGE:
# python:3.11-slim is used for:
# - Smaller image size (faster builds and deployments)
# - Faster startup compared to full Python images
# - Suitable for production and containerized environments

# SYSTEM DEPENDENCIES:
# These packages are required for scientific/data processing libraries:
# 1. build-essential → required to compile Python packages like numpy, pandas, scipy
# 2. curl → used for downloading files or debugging network requests
# 3. libpq-dev → PostgreSQL client libraries for database connectivity
# 4. tesseract-ocr → Optical Character Recognition (extract text from images)
# 5. poppler-utils → PDF processing utilities (used for PDF parsing/extraction)
#
# CLEANUP STEP:
# rm -rf /var/lib/apt/lists/* removes cached package lists
# → reduces final image size (important for production optimization)

# WORKDIR:
# WORKDIR /app sets the working directory inside the container
# This means:
# - A folder /app is created if it does not exist
# - All subsequent commands run relative to /app
# - Similar to running: cd /app

# DEPENDENCY INSTALLATION:
# First, requirements.txt is copied to leverage Docker layer caching
# Then dependencies are installed using pip
# --no-cache-dir prevents pip from storing cached packages
# → reduces image size

# APPLICATION CODE:
# COPY . . copies the entire project into /app inside the container

# ENVIRONMENT VARIABLES:
# PYTHONUNBUFFERED=1 → ensures logs are printed in real-time (no buffering)
# PORT=8000 → default port for the API service

# PORT EXPOSURE:
# EXPOSE 8000 does NOT publish the port to the host machine
# It only documents that the container listens on port 8000
# Actual exposure happens in docker-compose.yml using port mapping

# STARTUP COMMAND:
# Development mode (current):
# CMD runs Uvicorn with --reload for automatic code reloading during development
# → useful for local development only

# Production mode (recommended for deployment):
# Gunicorn should be used instead of Uvicorn reload
# CMD ["gunicorn", "-c", "gunicorn_conf.py", "main:app"]

FROM python:3.11-slim 

# Install system dependencies for scientific packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libpq-dev \
    tesseract-ocr \
    poppler-utils \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Expose port
EXPOSE 8000

# default DEVELOPMENT MODE (--reload for live code updates):
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# Start command using Gunicorn for production
#CMD ["gunicorn", "-c", "gunicorn_conf.py", "main:app"]

