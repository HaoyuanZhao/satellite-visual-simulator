FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY demo_data ./data
RUN mkdir -p /app/satellite_db_cache

ENV SATELLITE_DATA_ROOT=/app/data
ENV SATELLITE_DATA_PATH=/app/data/demo_constellation
ENV SATELLITE_DB_ROOT=/app/satellite_db_cache

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
