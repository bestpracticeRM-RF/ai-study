"""
Мониторинг дрейфа данных (Evidently) для Iris-модели.

reference = обучающий датасет Iris (то, на чём училась модель)
current   = «прод-данные». Для демо генерируются сэмплированием reference;
            параметром можно впрыснуть искусственный дрейф:
              airflow dags trigger iris_drift_monitoring --conf '{"inject_drift": true}'

Результат — метрики в VictoriaMetrics (POST /api/v1/import/prometheus):
  evidently_dataset_drift{dataset="iris"}                 0/1
  evidently_drift_share{dataset="iris"}                   доля дрейфнувших фич
  evidently_drifted_features{dataset="iris"}              счётчик
  evidently_feature_drift_score{dataset="iris",feature=}  score по каждой фиче
Алерты — VMRule (gitops/monitoring/drift-alerts.yaml).
"""

from __future__ import annotations

import pendulum
from airflow.decorators import dag, task

# numpy>=2.1: первые wheels под python3.13 (без него uv тянет numpy 2.0.2 sdist -> нужен gcc, его нет в образе)
PIP_REQS = ["evidently==0.6.7", "scikit-learn", "pandas", "numpy>=2.1"]
VM_IMPORT = ("http://vmsingle-vm-stack-victoria-metrics-k8s-stack"
             ".monitoring.svc.cluster.local:8428/api/v1/import/prometheus")


@dag(
    dag_id="iris_drift_monitoring",
    schedule=None,  # запуск руками; позже можно "@hourly"
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["mlops", "iris", "evidently", "monitoring", "phase3"],
    doc_md=__doc__,
)
def iris_drift_monitoring():
    @task.virtualenv(requirements=PIP_REQS, system_site_packages=False)
    def drift_report(inject_drift: bool, vm_import_url: str) -> dict:
        """Посчитать дрейф Evidently и запушить метрики в VictoriaMetrics."""
        import json
        import urllib.request

        import numpy as np
        import pandas as pd
        from evidently.report import Report
        from evidently.metric_preset import DataDriftPreset
        from sklearn.datasets import load_iris

        data = load_iris(as_frame=True)
        ref = data.frame.drop(columns=["target"])

        rng = np.random.default_rng(42)
        cur = ref.sample(n=100, replace=True, random_state=7).reset_index(drop=True)
        cur = cur + rng.normal(0, 0.05, cur.shape)  # лёгкий шум измерений
        if inject_drift:
            # искусственный дрейф: сдвигаем 2 фичи из 4
            cur["sepal length (cm)"] += 1.5
            cur["petal width (cm)"] *= 1.8

        report = Report(metrics=[DataDriftPreset()])
        report.run(reference_data=ref, current_data=cur)
        rd = report.as_dict()

        summary = next(m for m in rd["metrics"]
                       if m["metric"] == "DatasetDriftMetric")["result"]
        table = next(m for m in rd["metrics"]
                     if m["metric"] == "DataDriftTable")["result"]

        lines = [
            f'evidently_dataset_drift{{dataset="iris"}} {int(summary["dataset_drift"])}',
            f'evidently_drift_share{{dataset="iris"}} {summary["share_of_drifted_columns"]:.4f}',
            f'evidently_drifted_features{{dataset="iris"}} {summary["number_of_drifted_columns"]}',
        ]
        for feat, info in table["drift_by_columns"].items():
            f = feat.replace(" (cm)", "").replace(" ", "_")
            lines.append(
                f'evidently_feature_drift_score{{dataset="iris",feature="{f}"}} '
                f'{info["drift_score"]:.6f}')
            lines.append(
                f'evidently_feature_drift_detected{{dataset="iris",feature="{f}"}} '
                f'{int(info["drift_detected"])}')

        body = ("\n".join(lines) + "\n").encode()
        req = urllib.request.Request(vm_import_url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.status
        print(f"pushed {len(lines)} metrics, HTTP {code}")
        print(json.dumps({"dataset_drift": summary["dataset_drift"],
                          "share": summary["share_of_drifted_columns"]}))
        return {"dataset_drift": bool(summary["dataset_drift"]),
                "share": float(summary["share_of_drifted_columns"])}

    @task
    def report_status(res: dict) -> None:
        flag = "🔴 DRIFT" if res["dataset_drift"] else "🟢 no drift"
        print(f"{flag} share={res['share']:.2f}")

    @task
    def get_conf(**ctx) -> bool:
        conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
        return bool(conf.get("inject_drift", False))

    inject = get_conf()
    report_status(drift_report(inject, VM_IMPORT))


iris_drift_monitoring()
