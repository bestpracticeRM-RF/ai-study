# Фаза 2 — Первый Airflow DAG (Iris → MLflow)

> Дата: 2026-07-03. Обучающая станция (k3s, single-node). Не prod.
> Итог: рабочий end-to-end ML-пайплайн в Airflow, результат виден в MLflow.

## Что сделано (кратко)

1. **Написан DAG** `iris_training_pipeline` — load Iris → train → log в MLflow (артефакт в MinIO).
2. **Настроен gitSync** в Airflow — DAG-и тянутся из GitHub-репо.
3. **Починены 2 блокера по пути:** crashloop VictoriaMetrics-operator и Host-header 403 у MLflow.

Результат: эксперимент `Airflow_Iris_Pipeline` в MLflow, run с `accuracy=1.0`, модель в бакете `mlflow-artifacts`.

---

## 1. DAG: `dags/iris_training_dag.py`

**Зачем:** до этого Airflow был задеплоен (6 подов, KubernetesExecutor), но **пустой** — ни одного DAG. Пайплайнов не было. Это первый рабочий пайплайн — закрывает пункт 1 Фазы 2.

**Структура (TaskFlow API):**
- `check_config` — fail fast, если в task-под не проброшены env MLflow/S3.
- `train_and_log` — обучение LogisticRegression на Iris, лог параметров/метрики/модели в MLflow.
- `report` — печать итога.

**Почему `@task.virtualenv`:** базовый образ Airflow не содержит `mlflow`/`scikit-learn`. Тяжёлый шаг выполняется в изолированном venv, который ставит пакеты внутри task-пода на лету.

> ⚠️ Узкое место: venv-установка mlflow+deps занимает ~10 мин на каждый run. Приемлемо для демо. Для регулярных запусков — собрать свой Docker-образ с предустановленными пакетами и запускать через `KubernetesPodOperator` (Фаза 3 / оптимизация).

> ⚠️ Грабли: `@task.virtualenv` выполняется в отдельном subprocess — **глобали модуля не видны** внутри функции. Все константы (имя эксперимента и т.п.) объявляются ВНУТРИ функции. Иначе `NameError`.

---

## 2. Доставка DAG: gitSync

**Зачем именно gitSync:** при KubernetesExecutor каждая task — отдельный под. DAG должен попадать и в task-поды. На StorageClass `local-path` (RWO) шаринг каталога между подами невозможен. gitSync решает: каждый под клонирует свою копию из Git. Это индустриальный стандарт (GitOps-подход к DAG-ам).

**Конфиг (helm values Airflow):**
```yaml
dags:
  gitSync:
    enabled: true
    repo: https://github.com/bestpracticeRM-RF/ai-study.git
    branch: main
    subPath: dags
    period: 30s
```
Применено через `helm upgrade` (chart из кэша `~/.cache/helm/repository/airflow-1.21.0.tgz`, чтобы не менять версию и не триггерить миграции БД).

git-sync sidecar появляется в **dag-processor** (парсит DAG-и в Airflow 3.x) и triggerer. Репо публичный → креды не нужны.

**Рабочий цикл:** правишь DAG локально → `git push` → gitSync подтягивает за ~30с → Airflow перечитывает.

---

## 3. Блокер А — VictoriaMetrics operator CrashLoopBackOff

**Симптом:** оператор перезапускался 7763 раза. `cannot setup manager: failed to wait for scrapeconfig caches to sync ScrapeConfig timed out`.

**Причина:** оператор watch-ил Prometheus-CRD `ScrapeConfig`, которого нет в кластере (converter включён по дефолту, но Prometheus-CRD не установлены).

**Фикс (helm, закреплено в релизе):**
```bash
helm upgrade vm-stack vm/victoria-metrics-k8s-stack --version 0.76.0 -n monitoring \
  --reuse-values --set victoria-metrics-operator.operator.disable_prometheus_converter=true
```
Chart раскрывает флаг в per-CRD env `VM_ENABLEDPROMETHEUSCONVERTER_SCRAPECONFIG=false` (+ др.).

> Урок: в этой версии оператора converter управляется **per-CRD** переменными, а не общим `VM_ENABLEDPROMETHEUSCONVERTER`. Ручной патч общего флага не работает.

Попутно удалён сиротский CRD `servicemonitors.monitoring.coreos.com`, в `dcgm-exporter` выставлено `serviceMonitor.enabled=false` (метрики GPU идут через нативный `vmservicescrape`).

---

## 4. Блокер Б — MLflow отклонял svc-адрес (403)

**Симптом:** task-под падал через ~10 мин. MLflow отвечал `403 Invalid Host header - possible DNS rebinding attack detected` на запросы по внутрикластерному адресу `mlflow.mlops-tracking.svc.cluster.local`.

**Причина:** MLflow-сервер был запущен с `--allowed-hosts=mlflow.mlops.local` (только ingress-хост). Но DAG из кластера обязан ходить по svc-адресу (ingress-хост `mlflow.mlops.local` внутри кластера не резолвится).

**Осложнение:** chart mlflow 1.8.1 по дефолту (`log.enabled: true`) добавляет `--gunicorn-opts`, а MLflow запрещает security-флаги (`--allowed-hosts`) вместе с gunicorn — нужен uvicorn.

**Фикс — переустановлен MLflow начисто** (`mlflow-values.yaml`), данные сохранены (Postgres-БД `mlflow` + бакет MinIO `mlflow-artifacts` — внешние, uninstall их не трогает):
```yaml
log:
  enabled: false          # убирает --gunicorn-opts → MLflow идёт на default uvicorn
extraArgs:
  allowedHosts: "*"        # uvicorn принимает любой Host (для лабы ок)
```
```bash
helm uninstall mlflow -n mlops-tracking
helm install  mlflow ~/.cache/helm/repository/mlflow-1.8.1.tgz -n mlops-tracking -f mlflow-values.yaml
```
После: svc-адрес отвечает `200`, старые эксперименты (`Phase1_Test`, 3 runs) на месте.

> ⚠️ `allowedHosts: "*"` отключает DNS-rebinding-защиту. Ок для локальной обучающей станции, в prod — явный список хостов.

---

## Как перезапустить пайплайн

```bash
# UI:  http://airflow.mlops.local  → DAG iris_training_pipeline → Trigger
# CLI:
kubectl exec -n mlops-pipelines deploy/airflow-scheduler -c scheduler -- \
  airflow dags trigger iris_training_pipeline
```
Результат смотреть: `http://mlflow.mlops.local` → эксперимент `Airflow_Iris_Pipeline`.

---

## Файлы, затронутые в этой работе

| Файл | Что |
|---|---|
| `dags/iris_training_dag.py` | сам DAG (в Git, тянется gitSync) |
| `mlflow-values.yaml` | values нового MLflow-релиза (фикс Host) |
| `airflow-values.yaml` | базовые values Airflow (gitSync добавлен через `--set`) |
| `CLUSTER-AUDIT.md` | аудит кластера + история фиксов |

## Долг (не блокирует, но стоит закрыть)
- Секреты (`<REDACTED>`, `<REDACTED>`) — в открытом виде в values/коде. Вынести в K8s Secret / External Secrets Operator.
- venv-install 10 мин на run — собрать образ с mlflow+sklearn, перейти на `KubernetesPodOperator`.
- Зафиксировать gitSync-параметры Airflow и фиксы в Git-values (для Argo CD GitOps).
