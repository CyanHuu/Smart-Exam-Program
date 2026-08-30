FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

ENV DATA_TTL_SECONDS=86400 \
    MAX_UPLOAD_BYTES=20971520
EXPOSE 8000
CMD ["uvicorn", "api_service:app", "--host", "0.0.0.0", "--port", "8000"]
