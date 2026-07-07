# Real-time inference consumer: Kafka -> Triton (HTTP v2) -> Kafka.
# python:3.12-slim + tritonclient[http] + kafka-python. Без GPU — ходит в Triton по сервису.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    "kafka-python-ng==2.2.2" \
    "tritonclient[http]==2.57.0" \
    "numpy>=2.1"

WORKDIR /app
COPY kafka_consumer.py .

CMD ["python", "-u", "kafka_consumer.py"]
