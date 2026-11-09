FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
RUN python scripts/fetch_brand.py
RUN useradd -r -u 10001 appuser && mkdir -p /srv/data && chown -R appuser /srv/data
USER appuser
EXPOSE 8000
# One worker on purpose: the 5-minute poller runs inside the app process.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
