# Train-образ для Airflow DAG-ов (KubernetesExecutor pod_override).
# Всё ML-окружение предустановлено -> смерть 10-минутного venv-install в каждом run.
# Собирается CI (kaniko) -> registry в кластере.
FROM python:3.12-slim

RUN pip install --no-cache-dir \
    mlflow==3.7.0 \
    scikit-learn \
    "evidently>=0.7,<0.9" \
    pandas \
    "numpy>=2.1" \
    boto3

# airflow task-runner требует пакет apache-airflow в образе для @task;
# psycopg2 — task-под инициализирует ORM по env AIRFLOW__DATABASE__* (postgres)
RUN pip install --no-cache-dir "apache-airflow==3.2.0" apache-airflow-providers-cncf-kubernetes psycopg2-binary

WORKDIR /app
