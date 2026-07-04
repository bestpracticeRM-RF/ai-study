# Стек MLOps — учебный справочник (использование + мониторинг)

> Обучающая станция (k3s, single-node, RTX 5070 Ti). Не prod.
> Для каждой технологии: что это · доступ · базовые команды · как проверить здоровье/мониторить · где учиться.
> Домены: всё на `*.mlops.local` → `192.168.3.248` (нужны записи в `/etc/hosts` на хосте).
> k3s намеренно опущен (по запросу).

Общая шпаргалка kubectl (работает для любого компонента ниже):
```bash
kubectl get pods -n <namespace>                 # список подов
kubectl logs -n <ns> <pod> --tail=50 -f         # логи
kubectl describe pod -n <ns> <pod>              # события/причины проблем
kubectl get events -n <ns> --sort-by=.lastTimestamp | tail   # свежие события
helm list -A                                    # что установлено через Helm
```

---

## 1. Argo CD — GitOps (ns: `argocd`)

**Что:** «синхронизатор» — держит кластер в состоянии, описанном в Git. Меняешь манифест в репо → Argo применяет в кластер. Откат = откат коммита.

**Доступ (нет ingress, через port-forward):**
```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
# UI: https://localhost:8080
# логин admin, пароль:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```
**Базовое использование:** создаёшь `Application` (CRD), указываешь repo+path+namespace → Argo синкает.

**Мониторинг/health:**
```bash
kubectl get applications -n argocd          # статус синка (Synced/OutOfSync, Healthy/Degraded)
kubectl get pods -n argocd
```
В UI: карточки приложений — зелёное = Synced+Healthy.

**Учиться:** https://argo-cd.readthedocs.io — начни с "Getting Started" и концепции Application.

---

## 2. cert-manager — TLS-сертификаты (ns: `cert-manager`)

**Что:** автоматически выпускает и продлевает TLS-сертификаты (Let's Encrypt, self-signed, внутренний CA). Для HTTPS-ingress.

**Базовое использование:** создаёшь `ClusterIssuer` (кто выдаёт) + `Certificate` (что выдать) → cert-manager кладёт секрет с сертификатом.

**Мониторинг/health:**
```bash
kubectl get pods -n cert-manager                  # 3 пода: cert-manager, cainjector, webhook
kubectl get clusterissuers,certificates -A        # статус выпуска
kubectl describe certificate <name> -n <ns>       # почему не выпустился
```
Признак здоровья: `Certificate` в состоянии `Ready=True`.

**Учиться:** https://cert-manager.io/docs — раздел "Tutorials".

---

## 3. ingress-nginx — маршрутизация HTTP (ns: `ingress-nginx`)

**Что:** входная дверь кластера. По hostname (`mlflow.mlops.local` и т.п.) направляет трафик в нужный сервис.

**Базовое использование:** создаёшь `Ingress` с host+path+backend service.

**Мониторинг/health:**
```bash
kubectl get ingress -A                                    # все маршруты
kubectl get pods -n ingress-nginx
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller --tail=50   # видно 404/502/timeouts
```
Проверка снаружи: `curl -H 'Host: mlflow.mlops.local' http://192.168.3.248/health`.

**Учиться:** https://kubernetes.github.io/ingress-nginx — annotations reference особенно полезен.

---

## 4. PostgreSQL — база метаданных (ns: `mlops-infra`, под `postgresql-0`)

**Что:** реляционная БД. Здесь — хранилище метаданных MLflow (эксперименты, runs, метрики) и отдельная БД для Airflow.

**Доступ:**
```bash
kubectl exec -it -n mlops-infra postgresql-0 -- bash
# внутри:
PGPASSWORD=<pass> psql -U mlflow -d mlflow -c '\dt'    # таблицы
```
**Мониторинг/health:**
```bash
kubectl get pod -n mlops-infra postgresql-0
kubectl exec -n mlops-infra postgresql-0 -- pg_isready       # готовность
# размер БД, активные подключения — через psql: SELECT ... FROM pg_stat_activity;
```
Метрики Postgres при желании — через postgres-exporter в VictoriaMetrics (пока не стоит).

**Учиться:** https://www.postgresql.org/docs — базово `psql`, индексы, `EXPLAIN`.

---

## 5. MinIO — объектное хранилище S3 (ns: `mlops-infra`)

**Что:** локальный аналог AWS S3. Хранит артефакты моделей MLflow (бакет `mlflow-artifacts`) и model repository Triton (`triton-models`).

**Доступ:** UI/API `http://minio.mlops.local`. Клиент `mc` или boto3/aws-cli.
```bash
# через mc (внутри пода или локально):
mc alias set loc http://minio.mlops.local minioadmin <REDACTED>
mc ls loc                          # список бакетов
mc ls loc/mlflow-artifacts --recursive
```
**Мониторинг/health:**
```bash
kubectl get pod -n mlops-infra -l app=minio
curl -s http://minio.mlops.local/minio/health/live -o /dev/null -w '%{http_code}\n'   # 200 = жив
```
**Учиться:** https://min.io/docs — S3 API, бакеты, политики доступа.

---

## 6. MLflow — трекинг экспериментов и реестр моделей (ns: `mlops-tracking`)

**Что:** записывает параметры/метрики/артефакты обучения; реестр версий моделей. Backend — Postgres, артефакты — MinIO.

**Доступ:** UI `http://mlflow.mlops.local`.
**Использование (Python):**
```python
import mlflow
mlflow.set_tracking_uri("http://mlflow.mlops.local")   # с хоста
mlflow.set_experiment("my_exp")
with mlflow.start_run():
    mlflow.log_param("lr", 0.01); mlflow.log_metric("acc", 0.98)
```
Из кластера tracking_uri = `http://mlflow.mlops-tracking.svc.cluster.local`.

**Мониторинг/health:**
```bash
kubectl get pod -n mlops-tracking -l app.kubernetes.io/name=mlflow
curl -s http://mlflow.mlops.local/health -w ' %{http_code}\n'
# список экспериментов через API:
curl -s http://mlflow.mlops.local/api/2.0/mlflow/experiments/search -X POST -d '{"max_results":100}'
```
**Учиться:** https://mlflow.org/docs/latest — Tracking, Models, Model Registry.

---

## 7. Apache Airflow — оркестратор пайплайнов (ns: `mlops-pipelines`)

**Что:** запускает DAG-и (граф задач) по расписанию/вручную. У нас KubernetesExecutor — каждая задача = отдельный под. DAG-и тянутся из Git (gitSync).

**Доступ:** UI `http://airflow.mlops.local`.
**Использование (CLI внутри):**
```bash
kubectl exec -n mlops-pipelines deploy/airflow-scheduler -c scheduler -- airflow dags list
kubectl exec -n mlops-pipelines deploy/airflow-scheduler -c scheduler -- airflow dags trigger <dag_id>
kubectl exec -n mlops-pipelines deploy/airflow-scheduler -c scheduler -- \
  airflow tasks states-for-dag-run <dag_id> <run_id> -o plain
```
Правишь DAG локально → `git push` → gitSync подтянет за ~30с.

**Мониторинг/health:**
```bash
kubectl get pods -n mlops-pipelines            # scheduler, dag-processor, api-server, triggerer
kubectl logs -n mlops-pipelines deploy/airflow-dag-processor -c git-sync --tail=5   # синк DAG-ов
```
В UI: Grid view DAG-а — зелёные квадраты = success, красные = failed.

**Учиться:** https://airflow.apache.org/docs — Core Concepts, TaskFlow API, KubernetesExecutor.

---

## 8. VictoriaMetrics stack — метрики (ns: `monitoring`)

**Что:** сбор и хранение метрик (аналог Prometheus, но экономнее). Компоненты:
- **vmsingle** — хранилище метрик (TSDB)
- **vmagent** — собирает метрики со scrape-таргетов
- **vmalert** — правила алертов
- **vmalertmanager** — рассылка алертов
- **operator** — управляет конфигами через CRD (`VMServiceScrape` и т.п.)

**Использование:** таргеты задаются через CRD `VMServiceScrape`/`VMPodScrape`.
```bash
kubectl get vmservicescrapes -n monitoring        # что скрейпится
kubectl get vmrules -n monitoring                 # правила алертов
```
**Мониторинг самого мониторинга:**
```bash
kubectl get pods -n monitoring
# оператор должен быть без CrashLoop (была проблема — см. CLUSTER-AUDIT.md)
kubectl get vmservicescrapes -n monitoring | grep -c operational
```
Запрос метрик (PromQL) — обычно через Grafana (ниже) или напрямую в vmsingle.

**Учиться:** https://docs.victoriametrics.com + основы PromQL (https://prometheus.io/docs/prometheus/latest/querying/basics).

---

## 9. Grafana — визуализация метрик (ns: `monitoring`)

**Что:** дашборды и графики поверх метрик VictoriaMetrics.

**Доступ:** UI `http://grafana.mlops.local`.
```bash
# пароль admin:
kubectl get secret -n monitoring vm-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
```
**Использование:** Explore → PromQL-запрос; Dashboards → готовые/свои.
Полезные запросы:
```promql
up                                   # какие таргеты живы (1/0)
DCGM_FI_DEV_GPU_UTIL                  # загрузка GPU
DCGM_FI_DEV_GPU_TEMP                  # температура GPU
container_memory_usage_bytes         # память подов
```
**Мониторинг/health:** `kubectl get pod -n monitoring -l app.kubernetes.io/name=grafana`.

**Учиться:** https://grafana.com/docs — Panels, Dashboards, Explore.

---

## 10. DCGM exporter — метрики GPU (ns: `monitoring`)

**Что:** отдаёт метрики NVIDIA GPU (утилизация, память, температура, питание) в VictoriaMetrics.

**Мониторинг/health:**
```bash
kubectl get pod -n monitoring -l app.kubernetes.io/name=dcgm-exporter
# сырые метрики:
kubectl exec -n monitoring <dcgm-pod> -- curl -s localhost:9400/metrics | grep DCGM_FI_DEV_GPU_UTIL
```
Смотреть удобно в Grafana (метрики `DCGM_FI_DEV_*`).

**Учиться:** https://github.com/NVIDIA/dcgm-exporter — список метрик.

---

## 11. NVIDIA device plugin / GPU в кластере (ns: `kube-system`)

**Что:** делает GPU видимым для Kubernetes-шедулера. Под запрашивает `nvidia.com/gpu: 1` — получает карту.

**Мониторинг/health:**
```bash
kubectl get node -o json | grep nvidia.com/gpu        # capacity/allocatable
kubectl get pods -n kube-system | grep nvidia
nvidia-smi                                            # на хосте: карта, память, температура
```
Как запрашивать GPU в поде: `resources.limits: { nvidia.com/gpu: 1 }` + `runtimeClassName: nvidia`.

**Учиться:** https://github.com/NVIDIA/k8s-device-plugin.

---

## 12. NVIDIA Triton — serving моделей (ns: `mlops-serving`)

**Что:** сервер инференса. Отдаёт модели (ONNX/TensorRT/PyTorch...) по HTTP/gRPC, батчинг, GPU. Model repository — в MinIO (`triton-models`).

**Доступ:** `http://triton.mlops.local` (HTTP API :8000).
**Использование:**
```bash
curl -s http://triton.mlops.local/v2/health/ready -w ' %{http_code}\n'   # готов?
curl -s http://triton.mlops.local/v2/models/iris | python3 -m json.tool   # метаданные модели
# инференс: POST /v2/models/<name>/infer  (JSON с inputs)
```
**Мониторинг/health:**
```bash
kubectl get pods -n mlops-serving
kubectl logs -n mlops-serving deploy/triton --tail=40    # какие модели загрузились (READY)
curl -s http://triton.mlops.local/metrics | grep nv_inference   # метрики инференса (Prometheus)
```
Метрики Triton (порт 8002) можно скрейпить в VictoriaMetrics через `VMServiceScrape`.

**Учиться:** https://docs.nvidia.com/deeplearning/triton-inference-server — Model Repository, config.pbtxt, dynamic batching. Детали нашего деплоя: `docs/PHASE2-TRITON-SERVING.md`.

---

## 13. Helm — менеджер пакетов Kubernetes (инструмент, не под)

**Что:** ставит приложения в кластер из чартов (шаблоны манифестов + values).

**Базовые команды:**
```bash
helm list -A                                   # что установлено
helm get values <release> -n <ns>              # текущие values
helm history <release> -n <ns>                 # ревизии
helm upgrade <release> <chart> -n <ns> --reuse-values --set key=val   # обновить
helm rollback <release> <revision> -n <ns>     # откат
helm show values <repo>/<chart> --version X    # все доступные параметры чарта
```
> Урок из практики: `--reuse-values` сохраняет твои настройки при upgrade; всегда проверяй `helm history` перед изменениями.

**Учиться:** https://helm.sh/docs — Charts, Values, Templates.

---

## Быстрый «health-check всего стека»
```bash
# все проблемные поды (не Running/Ready):
kubectl get pods -A | grep -vE 'Running|Completed'
# рестарты (признак нестабильности):
kubectl get pods -A --sort-by='.status.containerStatuses[0].restartCount' | tail -15
# доступность сервисов с хоста:
for h in mlflow minio airflow grafana triton; do
  printf "%s: " $h; curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $h.mlops.local" http://192.168.3.248/ 2>/dev/null || echo down
done
```

## Порядок изучения (рекомендация для новичка)
1. **kubectl** базово (get/logs/describe) — без этого никуда.
2. **Helm** — как ставится/обновляется софт.
3. **MLflow** — самый простой вход в ML-часть.
4. **MinIO / PostgreSQL** — где лежат данные.
5. **Airflow** — оркестрация пайплайнов.
6. **Grafana + VictoriaMetrics + DCGM** — наблюдаемость.
7. **Triton** — serving.
8. **Argo CD** — GitOps (когда освоишь остальное).
9. **ingress-nginx / cert-manager** — сеть и TLS.
