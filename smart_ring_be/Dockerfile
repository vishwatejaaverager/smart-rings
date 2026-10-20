FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies required for OpenCV, video processing, and native C++ wheels
# libgomp1 is required by PyTorch (pulled in transitively by `inference`), without it
# `from inference import get_model` fails with "libgomp.so.1: cannot open shared object file".
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    g++ \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and assets
COPY src/ ./src/
COPY mcp/ ./mcp/
COPY vids/ ./vids/
COPY models/ ./models/
COPY app.py ./
COPY yolo11s-seg.pt ./

# Expose ports: 8000 (FastAPI Backend & Fire TV WS), 8501 (Alexa UI)
EXPOSE 8000 8501

# Default: Run FastAPI backend
CMD ["uvicorn", "src.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
