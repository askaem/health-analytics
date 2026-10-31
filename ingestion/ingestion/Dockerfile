FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY insidetracker_import.py /app/insidetracker_import.py

# Run as a nonroot user. The mounted CSV is read-only.
RUN useradd -r -u 10001 appuser
USER appuser
ENTRYPOINT ["python", "/app/insidetracker_import.py"]
