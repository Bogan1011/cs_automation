FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
# rclone: makes the OneDrive log folders and links (signed in once with `rclone config`)
RUN apt-get update && apt-get install -y --no-install-recommends rclone ca-certificates && rm -rf /var/lib/apt/lists/*
ENV RCLONE_CONFIG=/srv/data/rclone.conf
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY troubleshooting ./troubleshooting
RUN python scripts/fetch_brand.py
RUN useradd -r -u 10001 appuser && mkdir -p /srv/data && chown -R appuser /srv/data
USER appuser
EXPOSE 8000
# One worker on purpose: the 5-minute poller runs inside the app process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
