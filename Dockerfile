FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 DEALROOM_DATA_DIR=/app/data
# one worker: each visitor's private copy is kept open in memory; threads serve requests in parallel
CMD gunicorn -w 1 --threads 8 --timeout 180 -b 0.0.0.0:${PORT:-8000} run:app
