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

# evidently>=0.7: старые 0.6.x требуют numpy<2.1, а под python3.13 wheels numpy
# начинаются с 2.1 (в airflow-образе нет gcc для сборки sdist) -> только новый API 0.7+.
PIP_REQS = ["evidently>=0.7,<0.9", "scikit-learn", "pandas", "numpy>=2.1"]
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
        """Посчитать дрейф Evidently (API 0.7+) и запушить метрики в VictoriaMetrics."""
        import json
        import re
        import urllib.request

        import numpy as np
        from evidently import Report
        from evidently.presets import DataDriftPreset
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

        snapshot = Report([DataDriftPreset()]).run(
            reference_data=ref, current_data=cur)
        rd = snapshot.dict()

        share, count = 0.0, 0
        feature_scores = {}
        for m in rd.get("metrics", []):
            mid = m.get("metric_id", "")
            val = m.get("value")
            if mid.startswith("DriftedColumnsCount"):
                count = int(val.get("count", 0))
                share = float(val.get("share", 0.0))
            elif mid.startswith("ValueDrift(column="):
                feat = re.search(r"column=([^)]+)", mid).group(1)
                f = feat.replace(" (cm)", "").replace(" ", "_")
                feature_scores[f] = float(val)

        dataset_drift = int(share >= 0.5)  # порог evidently по умолчанию
        lines = [
            f'evidently_dataset_drift{{dataset="iris"}} {dataset_drift}',
            f'evidently_drift_share{{dataset="iris"}} {share:.4f}',
            f'evidently_drifted_features{{dataset="iris"}} {count}',
        ]
        for f, score in feature_scores.items():
            lines.append(
                f'evidently_feature_drift_score{{dataset="iris",feature="{f}"}} '
                f'{score:.6f}')

        body = ("\n".join(lines) + "\n").encode()
        req = urllib.request.Request(vm_import_url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            code = resp.status
        print(f"pushed {len(lines)} metrics, HTTP {code}")
        print(json.dumps({"dataset_drift": dataset_drift, "share": share,
                          "drifted": count, "features": feature_scores}))
        return {"dataset_drift": bool(dataset_drift), "share": share}

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
