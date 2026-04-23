# Production-grade Dockerfile for ResearchHub Research Intelligence
FROM python:3.11-slim

# Install system dependencies for scientific packages
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    libpq-dev \
    tesseract-ocr \
    poppler-utils \
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

# Start command using Gunicorn for production
CMD ["gunicorn", "-c", "gunicorn_conf.py", "main:app"]
