# 🔍 Аудит кластера и системы — MLOps Platform

> Снимок состояния на **2026-07-03**. Источник — живой опрос кластера (`kubectl`, `helm`, `nvidia-smi`, `lscpu`).
> Обновлять при изменениях инфраструктуры.

---

## 🖥 Железо (host)

| Компонент | Значение |
|---|---|
| **CPU** | AMD Ryzen 7 5700X3D (8C/16T) |
| **RAM** | ~31 GiB (30.2 GB) |
| **GPU** | NVIDIA GeForce RTX 5070 Ti, 16 GB VRAM |
| **GPU драйвер** | 595.71.05 |
| **GPU темп / память** | 44°C / 749 MiB занято из 16303 MiB (idle) |
| **Диск** | NVMe `/dev/nvme0n1p2`, 1.9 TB, занято 435 GB (25%), свободно 1.4 TB |
| **OS** | Ubuntu 26.04 LTS |
| **Kernel** | 7.0.0-27-generic |
| **Node IP** | 192.168.3.248 |

---

## ☸️ Кластер

| Параметр | Значение |
|---|---|
| **Дистрибутив** | k3s `v1.35.4+k3s1` (single-node, control-plane) |
| **kubectl client** | v1.36.0 |
| **Container runtime** | containerd 2.2.3-k3s1 |
| **Node** | `btk-system-product-name`, Ready, uptime 61d |
| **Capacity** | cpu 16, memory 31718700Ki, pods 110, `nvidia.com/gpu: 1` |
| **StorageClass** | `local-path` (единственный, RWO) |

### GPU в кластере
- NVIDIA device plugin `v0.17.0` (CDI) — GPU проброшен, `nvidia.com/gpu: 1` виден шедулеру.
- DCGM exporter `4.5.2-4.8.1` — метрики GPU в VictoriaMetrics.

---

## 📦 Namespaces (10)

| Namespace | Возраст | Назначение |
|---|---|---|
| `argocd` | 61d | GitOps |
| `cert-manager` | 61d | TLS |
| `ingress-nginx` | 61d | Ingress-контроллер |
| `mlops-infra` | 61d | PostgreSQL + MinIO |
| `mlops-tracking` | 61d | MLflow |
| `mlops-pipelines` | 47d | **Airflow (задеплоен!)** |
| `monitoring` | 61d | VictoriaMetrics + Grafana |
| `kube-system` / `kube-public` / `kube-node-lease` | 61d | Системные |

---

## 🎯 Helm-релизы (9)

| Release | NS | Chart | App version |
|---|---|---|---|
| argocd | argocd | argo-cd-9.5.11 | v3.3.9 |
| cert-manager | cert-manager | cert-manager-v1.20.2 | v1.20.2 |
| ingress-nginx | ingress-nginx | ingress-nginx-4.15.1 | 1.15.1 |
| postgresql | mlops-infra | postgresql-18.6.2 | 18.3.0 |
| minio | mlops-infra | minio-5.4.0 | 2024-12-18 |
| mlflow | mlops-tracking | mlflow-1.8.1 | **3.7.0** (rev 6) |
| **airflow** | mlops-pipelines | airflow-1.21.0 | **3.2.0** |
| vm-stack | monitoring | victoria-metrics-k8s-stack-0.76.0 | v1.142.0 |
| dcgm-exporter | monitoring | dcgm-exporter-4.8.1 | 4.8.1 |

---

## 🌐 Ingress (домены `.mlops.local` → 192.168.3.248)

| Host | Сервис | Namespace |
|---|---|---|
| `mlflow.mlops.local` | MLflow UI | mlops-tracking |
| `minio.mlops.local` | MinIO API | mlops-infra |
| `airflow.mlops.local` | Airflow UI | mlops-pipelines |
| `grafana.mlops.local` | Grafana | monitoring (⚠️ без ingressClass) |

> Для доступа с хоста нужны записи в `/etc/hosts`: `192.168.3.248 mlflow.mlops.local minio.mlops.local airflow.mlops.local grafana.mlops.local`

---

## 💾 Хранилище (PVC, все `local-path` RWO)

| PVC | NS | Размер |
|---|---|---|
| data-postgresql-0 | mlops-infra | 8Gi |
| minio | mlops-infra | 20Gi |
| data-airflow-postgresql-0 | mlops-pipelines | 8Gi |
| logs-airflow-triggerer-0 | mlops-pipelines | **100Gi** |
| vmsingle-...-victoria-metrics | monitoring | 20Gi |

---

## ✅ Что реально работает (факт vs plan-AI.md)

`plan-AI.md` устарел — **Фаза 2 частично выполнена**, но в плане помечена как «будущее».

| Компонент | План | Факт |
|---|---|---|
| Argo CD, Cert-Manager | Фаза 1 ✅ | ✅ работает |
| GPU/CDI, DCGM | Фаза 1 ✅ | ✅ работает |
| MLflow + Postgres + MinIO | Фаза 1 ✅ | ✅ работает |
| VictoriaMetrics + Grafana | Фаза 1 ✅ | ⚠️ работает, но оператор в crashloop (см. ниже) |
| **Airflow (KubernetesExecutor)** | Фаза 2 (todo) | ✅ **УЖЕ задеплоен 47d** — но 0 DAG-ов |
| **Seldon Core / Triton serving** | Фаза 2 (todo) | ❌ не установлено |
| **GitOps: Argo CD ← Git-репо** | Фаза 2 (todo) | ❌ не настроено |
| DVC / model monitoring / CI-CD | Фаза 3 | ❌ не начато |

---

## ⚠️ Проблемы (требуют внимания)

### 1. VictoriaMetrics operator — CrashLoopBackOff (критично)
`vm-stack-victoria-metrics-operator` — **7758 рестартов**. Логи:
```
error setup: cannot setup manager: cannot start controller manager:
failed to wait for scrapeconfig caches to sync kind source:
*v1alpha1.ScrapeConfig: timed out waiting for cache to be synced for Kind *v1alpha1.ScrapeConfig
```
**Причина:** оператор не может синхронизировать кэш CRD `ScrapeConfig` (`operator.victoriametrics.com/v1alpha1`). Вероятно CRD отсутствует/устарел, либо не хватает RBAC на watch этого ресурса.
**Проверить:**
```bash
kubectl get crd | grep scrapeconfig
kubectl get clusterrole -o yaml | grep -A5 scrapeconfig
```
**Итог:** метрики частично собираются (vmsingle/vmagent живы), но оператор не примиряет конфиги. Динамические изменения мониторинга не применяются.

### 2. Airflow без DAG-ов
Поды Airflow здоровы (api-server, scheduler, dag-processor, triggerer, statsd, postgresql), но:
- `dags.gitSync.enabled: false` в `airflow-values.yaml`
- каталог DAG-ов пуст → **пайплайнов нет**

Airflow = «пустой движок». Следующий шаг Фазы 2 = дать ему DAG (обучение Iris → MLflow).

### 3. 🔒 Секреты MinIO в открытом виде (security)
В `test_mlflow.py` и `airflow-values.yaml` креды захардкожены:
```
AWS_ACCESS_KEY_ID = "minioadmin"
AWS_SECRET_ACCESS_KEY = "minioadmin123"
```
Попадают в Git-историю. Противоречит `agent.md` (требование ESO + Vault/Lockbox).
**Fix:** вынести в Kubernetes Secret / External Secrets Operator; сменить дефолтные креды.

### 4. Высокие RESTARTS по всему кластеру
Многие поды имеют сотни рестартов (argocd-repo-server 296, mlflow 150, cert-manager-webhook 199). Для single-node лабы с перезагрузками хоста — норма (все «14m ago» = один общий рестарт узла). Не критично, но стоит проверить, нет ли OOM: RAM всего 31 GiB на весь стек + GPU-нагрузки.

---

## 🎯 Рекомендованный следующий шаг

Закрыть Фазу 2 в правильном порядке:
1. **Починить VM-operator** (мониторинг — фундамент, чинить первым).
2. **Airflow DAG**: `load Iris → train → log в MLflow → артефакт в MinIO`. Через gitSync или baked-in dags. Закрывает gap «Airflow пустой» + подтверждает строку резюме про DAG-и.
3. **Serving**: поставить Seldon Core v2 (или Triton — резюме упоминает Triton, а его нет; лучше реально поднять). Эндпоинт inference для Iris.
4. **GitOps**: перенести все Helm-values в Git, подключить к Argo CD. Убрать ручной `helm install`.
5. **Security**: секреты в ESO/Vault.

---
*Файл сгенерирован автоматически на основе аудита кластера. Обновлять `kubectl`-опросом при изменениях.*
