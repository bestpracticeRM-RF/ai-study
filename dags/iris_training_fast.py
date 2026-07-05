"""
Iris training v2 — БЫСТРЫЙ: готовый train-образ вместо @task.virtualenv.

Отличие от iris_training_pipeline (v1):
  v1: базовый airflow-образ + venv с pip install в КАЖДОМ run (~10-12 мин)
  v2: образ registry.mlops.local/mlops/train (mlflow/sklearn предустановлены,
      собирает CI kaniko) через executor_config pod_override (~30-60 сек)

Цепочка: GitLab CI build -> registry -> нода тянет по registries.yaml -> KubernetesExecutor.
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task
from kubernetes.client import models as k8s

TRAIN_IMAGE = "registry.mlops.local/mlops/train:latest"

train_pod = {
    "pod_override": k8s.V1Pod(
        spec=k8s.V1PodSpec(
            containers=[k8s.V1Container(name="base", image=TRAIN_IMAGE)]
        )
    )
}


@dag(
    dag_id="iris_training_fast",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["mlops", "iris", "mlflow", "fast", "phase3"],
    doc_md=__doc__,
)
def iris_training_fast():
    @task(executor_config=train_pod)
    def train_and_log() -> dict:
        """Обучение + лог в MLflow. Все импорты уже в образе — venv не нужен."""
        import os

        import mlflow
        from mlflow.models.signature import infer_signature
        from sklearn.datasets import load_iris
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split

        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment("Airflow_Iris_Fast")

        X, y = load_iris(return_X_y=True)
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)

        with mlflow.start_run(run_name="fast_image_run") as run:
            model = LogisticRegression(max_iter=300).fit(X_tr, y_tr)
            acc = model.score(X_te, y_te)
            mlflow.log_param("max_iter", 300)
            mlflow.log_metric("accuracy", acc)
            sig = infer_signature(X_te, model.predict(X_te))
            mlflow.sklearn.log_model(model, name="model", signature=sig)
            print(f"run_id={run.info.run_id} accuracy={acc:.4f}")
            return {"run_id": run.info.run_id, "accuracy": acc}

    @task
    def report(res: dict) -> None:
        print(f"✅ fast pipeline: acc={res['accuracy']:.4f} run={res['run_id']}")

    report(train_and_log())


iris_training_fast()
