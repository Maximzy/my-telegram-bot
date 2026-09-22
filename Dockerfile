FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY main.py payment_service.py db_compat.py miniapp.html ./
COPY attached_assets/ ./attached_assets/

# Create data directory for SQLite (used when DATABASE_URL is not set)
RUN mkdir -p /app/data

ENV PORT=5000
ENV DATA_DIR=/app/data

EXPOSE 5000

CMD ["python", "main.py"]