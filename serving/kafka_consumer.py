#!/usr/bin/env python3
"""
Real-time inference consumer (запускается в k8s-поде, см. kafka-consumer.yaml).

Поток:
  Kafka topic `iris-requests`  --(сообщение: JSON {id, features})-->  consumer
    -> POST /v2/models/iris/infer  в Triton (HTTP v2 / KServe protocol)
    -> JSON {id, label, probabilities}
  Kafka topic `iris-responses` <-- publish

Семантика доставки: at-least-once (enable.auto.commit=true, auto-commit после poll).
Для exactly-once тут потребовалось бы: transactions API + idempotent producer +
внешнее хранилище результатов по id (дедупликация). Для лабы — at-least-once.

Переменные окружения:
  KAFKA_BOOTSTRAP   — bootstrap-сервер (default: kafka-cluster-kafka-bootstrap.kafka:9092)
  REQUEST_TOPIC     — default iris-requests
  RESPONSE_TOPIC    — default iris-responses
  GROUP_ID          — default iris-consumer
  TRITON_URL        — default http://triton.mlops-serving:8000
  MODEL_NAME        — default iris
"""
import os
import json
import time
import logging

from kafka import KafkaConsumer, KafkaProducer
import tritonclient.http as httpclient
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("iris-consumer")

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka-cluster-kafka-bootstrap.kafka:9092")
REQUEST_TOPIC = os.environ.get("REQUEST_TOPIC", "iris-requests")
RESPONSE_TOPIC = os.environ.get("RESPONSE_TOPIC", "iris-responses")
GROUP_ID = os.environ.get("GROUP_ID", "iris-consumer")
TRITON_URL = os.environ.get("TRITON_URL", "http://triton.mlops-serving:8000")
MODEL_NAME = os.environ.get("MODEL_NAME", "iris")

# KServe v2 label для iris (config.pbtxt: output "label" TYPE_INT64, "probabilities" FP32)
SPECIES = ["setosa", "versicolor", "virginica"]


def wait_for_kafka():
    """Kafka может стартовать позже consumer-пода — retry-loop на подключение."""
    while True:
        try:
            admin = KafkaConsumer(bootstrap_servers=KAFKA_BOOTSTRAP, request_timeout_ms=5000)
            admin.topics()  # metadata fetch
            log.info("Kafka доступен по %s", KAFKA_BOOTSTRAP)
            return
        except Exception as e:
            log.warning("Жду Kafka (%s): %s", KAFKA_BOOTSTRAP, e)
            time.sleep(5)


def wait_for_triton(triton):
    """Triton может быть занят загрузкой моделей — ждём /v2/health/ready."""
    for _ in range(60):
        try:
            if triton.is_server_ready():
                log.info("Triton готов: %s", TRITON_URL)
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("Triton не стал ready за 120s")


def main():
    wait_for_kafka()

    consumer = KafkaConsumer(
        REQUEST_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda d: json.dumps(d).encode("utf-8"),
        acks="all",
    )

    triton = httpclient.InferenceServerClient(url=TRITON_URL, verbose=False)
    wait_for_triton(triton)

    log.info("Стартую consume: %s -> Triton -> %s", REQUEST_TOPIC, RESPONSE_TOPIC)
    for msg in consumer:
        req = msg.value
        rid = req.get("id", "?")
        feats = req["features"]  # [4 float]
        try:
            arr = np.array([feats], dtype=np.float32)
            inp = httpclient.InferInput("X", arr.shape, "FP32")
            inp.set_data_from_numpy(arr)
            out_label = httpclient.InferRequestedOutput("label")
            out_prob = httpclient.InferRequestedOutput("probabilities")
            res = triton.infer(MODEL_NAME, inputs=[inp], outputs=[out_label, out_prob])
            label = int(res.as_numpy("label")[0])
            probs = res.as_numpy("probabilities")[0].tolist()
            response = {
                "id": rid,
                "label": label,
                "species": SPECIES[label] if 0 <= label < 3 else "unknown",
                "probabilities": [round(p, 4) for p in probs],
            }
            producer.send(RESPONSE_TOPIC, response)
            producer.flush()
            log.info("id=%s -> %s (probs=%s)", rid, response["species"], response["probabilities"])
        except Exception as e:
            log.error("id=%s failed: %s", rid, e)


if __name__ == "__main__":
    main()
