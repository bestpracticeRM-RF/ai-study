# Train-образ для Airflow DAG-ов (KubernetesExecutor pod_override).
# База — ОФИЦИАЛЬНЫЙ airflow-образ: тот же uid 50000, entrypoint, версии airflow/драйверов,
# что и у остального Airflow. Кастомный python:slim ловил цепочку граблей:
# psycopg2 -> asyncpg -> "uid 50000 not found". База apache/airflow снимает весь класс проблем.
# Сверху — только ML-зависимости. Собирает CI (kaniko) -> registry кластера.
FROM apache/airflow:3.2.0

RUN pip install --no-cache-dir \
    mlflow==3.7.0 \
    scikit-learn \
    "evidently>=0.7,<0.9" \
    pandas \
    "numpy>=2.1" \
    boto3
