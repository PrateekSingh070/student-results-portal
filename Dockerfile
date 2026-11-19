FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RESULTS_DB=/data/results.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system portal \
    && useradd --system --gid portal portal \
    && mkdir /data \
    && chown portal:portal /data

COPY portal ./portal
COPY seed.py .

USER portal
EXPOSE 8080
CMD ["python", "-m", "portal", "--host", "0.0.0.0"]
