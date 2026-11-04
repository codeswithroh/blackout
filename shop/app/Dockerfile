FROM python:3.12-slim
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn==0.30.6 httpx==0.27.2
WORKDIR /srv
COPY *.py .
ENV PYTHONUNBUFFERED=1
