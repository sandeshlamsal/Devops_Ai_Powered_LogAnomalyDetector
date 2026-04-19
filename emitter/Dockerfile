FROM python:3.11-slim

WORKDIR /app

COPY emitter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY emitter/ .
COPY config.yaml /config.yaml

CMD ["python", "emitter.py"]
