FROM python:3.11-slim

WORKDIR /app

# Install system build dependencies (for bcrypt, mysql, etc if compiling is needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Ensure uploads directory exists
RUN mkdir -p uploads

# Expose FastAPI server port
EXPOSE 8000

# Set environment variables for production execution
ENV PORT=8000
ENV DEV_MODE=false

# Start server using standard production ASGI workers
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
