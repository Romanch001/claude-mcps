FROM python:3.11-slim

WORKDIR /app

# cryptography package needs build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev cargo \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8000
CMD ["python", "server.py"]
