"""
Iris training pipeline: load -> train -> log to MLflow (артефакт в MinIO/S3).

Airflow 3.2.0, KubernetesExecutor. Каждая task = отдельный под из airflow-образа.
Base-образ не содержит mlflow/sklearn, поэтому тяжёлые шаги идут через
@task.virtualenv — venv с нужными пакетами ставится внутри task-пода на лету.

Доступы к MLflow/MinIO берутся из env, проброшенных в поды через airflow helm values
(MLFLOW_TRACKING_URI, MLFLOW_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY).
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task

EXPERIMENT_NAME = "Airflow_Iris_Pipeline"
PIP_REQS = ["mlflow==3.7.0", "scikit-learn", "boto3"]


@dag(
    dag_id="iris_training_pipeline",
    schedule=None,  # запуск вручную (trigger). Позже можно "@daily".
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["mlops", "iris", "mlflow", "phase2"],
    doc_md=__doc__,
)
def iris_training_pipeline():
    @task
    def check_config() -> dict:
        """Проверка доступности env (fail fast, если доступы не проброшены)."""
        import os

        cfg = {
            "tracking_uri": os.environ.get("MLFLOW_TRACKING_URI", ""),
            "s3_endpoint": os.environ.get("MLFLOW_S3_ENDPOINT_URL", ""),
        }
        if not cfg["tracking_uri"]:
            raise ValueError("MLFLOW_TRACKING_URI не задан в env task-пода")
        print(f"MLflow: {cfg['tracking_uri']} | S3: {cfg['s3_endpoint']}")
        return cfg

    @task.virtualenv(requirements=PIP_REQS, system_site_packages=False)
    def train_and_log(cfg: dict) -> dict:
        """Обучить LogisticRegression на Iris, залогировать run + модель в MLflow.

        ВАЖНО: @task.virtualenv выполняет функцию в изолированном subprocess —
        глобали модуля (EXPERIMENT_NAME и т.п.) недоступны. Всё нужное объявляем
        внутри функции.
        """
        import mlflow
        from mlflow.models.signature import infer_signature
        from sklearn.datasets import load_iris
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split

        experiment_name = "Airflow_Iris_Pipeline"

        mlflow.set_tracking_uri(cfg["tracking_uri"])
        mlflow.set_experiment(experiment_name)

        X, y = load_iris(return_X_y=True)
        X_tr, X_te, y_tr, y_te = train_test_split(
            X, y, test_size=0.2, random_state=42
        )

        max_iter = 300
        with mlflow.start_run(run_name="airflow_iris") as run:
            model = LogisticRegression(max_iter=max_iter)
            model.fit(X_tr, y_tr)
            acc = model.score(X_te, y_te)

            mlflow.log_param("max_iter", max_iter)
            mlflow.log_param("model", "LogisticRegression")
            mlflow.log_metric("accuracy", acc)

            sig = infer_signature(X_te, model.predict(X_te))
            mlflow.sklearn.log_model(model, name="model", signature=sig)

            run_id = run.info.run_id
            print(f"run_id={run_id} accuracy={acc:.4f}")
            return {"run_id": run_id, "accuracy": acc}

    @task
    def report(result: dict) -> None:
        print(f"✅ Pipeline done. run_id={result['run_id']} acc={result['accuracy']:.4f}")

    cfg = check_config()
    result = train_and_log(cfg)
    report(result)


iris_training_pipeline()
