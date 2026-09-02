FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt
COPY cft ./cft
CMD ["uvicorn", "cft.main:app", "--host", "0.0.0.0", "--port", "8000"]
