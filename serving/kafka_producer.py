#!/usr/bin/env python3
"""
Демо-producer для real-time inference пайплайна (аналог serving/test_infer.py,
но публикует в Kafka, а не дёргает Triton напрямую).

Запуск (с хоста, через kubectl-port-forward к Kafka):
  kubectl port-forward -n kafka svc/kafka-cluster-kafka-bootstrap 19092:9092 &
  KAFKA_BOOTSTRAP=localhost:19092 python3 serving/kafka_producer.py [N]

По умолчанию публикует 10 запросов с известными примерами Iris:
  setosa [5.1,3.5,1.4,0.2] -> ожидаем label 0
  virginica [6.7,3.0,5.2,2.3] -> ожидаем label 2
Чередует их. После — подписывается на iris-responses и печатает ответы (timeout 15s).
"""
import os
import sys
import json
import time
import uuid
import logging

from kafka import KafkaProducer, KafkaConsumer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("iris-producer")

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092")
REQUEST_TOPIC = os.environ.get("REQUEST_TOPIC", "iris-requests")
RESPONSE_TOPIC = os.environ.get("RESPONSE_TOPIC", "iris-responses")

# 2 эталонных примера (как в test_infer.py)
SAMPLES = [
    {"features": [5.1, 3.5, 1.4, 0.2], "expect": "setosa"},
    {"features": [6.7, 3.0, 5.2, 2.3], "expect": "virginica"},
]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda d: json.dumps(d).encode("utf-8"),
        acks="all",
    )

    sent_ids = []
    for i in range(n):
        s = SAMPLES[i % len(SAMPLES)]
        rid = str(uuid.uuid4())
        producer.send(REQUEST_TOPIC, {"id": rid, "features": s["features"]})
        sent_ids.append((rid, s["expect"]))
    producer.flush()
    log.info("Опубликовано %d сообщений в %s", n, REQUEST_TOPIC)

    # Подписываемся на ответы, ловим ровно столько, сколько отправили
    consumer = KafkaConsumer(
        RESPONSE_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=f"producer-demo-{uuid.uuid4().hex[:6]}",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
        consumer_timeout_ms=15000,
    )

    expected = dict(sent_ids)
    got = {}
    log.info("Жду ответы в %s (timeout 15s)...", RESPONSE_TOPIC)
    for msg in consumer:
        r = msg.value
        rid = r.get("id")
        got[rid] = r
        exp = expected.get(rid, "?")
        log.info("id=%s species=%s probs=%s (ожидал %s)",
                 rid, r.get("species"), r.get("probabilities"), exp)
        if len(got) >= n:
            break

    ok = sum(1 for rid, exp in sent_ids if got.get(rid, {}).get("species") == exp)
    log.info("Итог: %d/%d предсказаний совпали с ожиданием.", ok, n)
    if ok != n:
        sys.exit(1)


if __name__ == "__main__":
    main()
