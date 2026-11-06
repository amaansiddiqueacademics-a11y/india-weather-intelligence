# App image: the Python pipeline and the dashboard. The database runs in its own container (docker-compose.yml).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY config ./config
COPY sql ./sql
COPY src ./src
COPY web ./web
COPY models ./models
COPY reports ./reports
COPY tests ./tests
COPY pytest.ini README.md ./

WORKDIR /app/src
EXPOSE 8077
CMD ["python", "-m", "uvicorn", "wx.api:app", "--host", "0.0.0.0", "--port", "8077"]
