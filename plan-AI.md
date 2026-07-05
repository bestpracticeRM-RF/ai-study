# MLOps Roadmap: статус платформы и план обучения

> Обновлено: 2026-07-05. Прошлый план устарел (Фаза 2 значилась «будущим») — этот отражает факт.
> Детали каждого пункта — в `docs/PHASE*.md` (11 конспектов). Доступы — `docs/ACCESS.md`.

## ✅ Фаза 1 — Инфраструктура (завершена)
k3s (single-node, RTX 5070 Ti) · Argo CD · cert-manager · ingress-nginx ·
PostgreSQL · MinIO · MLflow · VictoriaMetrics+Grafana · DCGM · GPU через CDI.

## ✅ Фаза 2 — Пайплайны и serving (завершена)
- **Airflow DAG** `iris_training_pipeline`: обучение → MLflow (gitSync из GitHub)
- **Triton Inference Server**: iris (CPU) + resnet50 (GPU), model repo в MinIO
- **GitOps**: Argo CD управляет triton/minio/airflow/monitoring/platform/jupyterhub
- Починен VM-operator (7763 краша), MLflow Host-403, single-GPU Recreate

## ✅ Фаза 3 — Прод-практики (завершена)
| Что | Итог | Дока |
|---|---|---|
| Vault + ESO | секреты вне git/values; 8 записей в `secret/mlops/*` | PHASE3-VAULT-ESO, -ESO-MIGRATION |
| Evidently | дрейф → VictoriaMetrics → алерт `IrisDatasetDrift` firing | PHASE3-EVIDENTLY |
| GPU time-slicing | 1 GPU → 4 виртуальных (Triton + ноутбуки) | в PHASE3-* |
| JupyterHub | ноутбуки в кластере, GPU-профиль, auth из Vault | ACCESS.md |
| GitLab CE + runner | self-hosted, CI в подах кластера | PHASE3-GITLAB |
| Registry + kaniko | commit → образ → registry → нода тянет | PHASE3-CI-IMAGE |
| Быстрый train-DAG | **12 мин → 85 сек** (готовый образ, pod_override) | PHASE3-CI-IMAGE |
| DVC | версии датасетов в MinIO, откат проверен | PHASE3-DVC |

Воспроизводимость: код (git) + данные (DVC) + окружение (образ) + инфра (GitOps) + секреты (Vault).

---

## 🎓 Фаза 4 — ТЕКУЩАЯ: теория к собеседованиям + углубление в инструменты

Платформа готова = полигон. Теперь режим: **теория → проверка руками на своём кластере → разбор как на собесе**.

### Блоки теории (порядок = приоритет на собесах РФ)
1. **Kubernetes глубоко**: scheduling, QoS/eviction, PDB, HPA/VPA, NetworkPolicy,
   RBAC, CNI/CSI/CRI, отладка (CrashLoop/OOM/Pending — у нас всё это было вживую!)
2. **MLOps-архитектура**: feature store, training/serving skew, online/offline инференс,
   canary/shadow-деплой моделей, model registry workflow, retraining-триггеры
3. **Мониторинг**: PromQL свободно, SLI/SLO/error budget, алертинг-дизайн,
   drift vs performance monitoring (Evidently — уже есть база)
4. **Serving**: Triton (batching, ensembles, TensorRT), KServe vs Seldon,
   GPU-шеринг (time-slicing vs MIG — щупали), vLLM/TGI для LLM
5. **CI/CD+GitOps**: GitLab CI паттерны, kaniko/buildah, Argo CD (sync waves,
   app-of-apps, drift), стратегии релиза моделей
6. **Данные**: DVC vs lakeFS, форматы (parquet/arrow), Kafka в ML-контурах
7. **Security/Compliance**: Vault-паттерны (transit auto-unseal — разбирали),
   ESO, 152-ФЗ контуры, image signing (cosign), RBAC-аудит

### Формат занятия (предложение)
Тема → 30-60 мин теории с вопросами «как на собесе» → практика на кластере → конспект в `docs/theory/`.

### Технический долг платформы (фон, по мере надобности)
- Deploy-стадия CI: bump тега → Argo (полный релизный цикл модели)
- mlflow chart → 1.11.x (DB-пароль из ESO → mlflow в Argo)
- TLS на ingress (cert-manager простаивает) · app-of-apps · Argo для vault/vm-stack
- Ротация утёкшего пароля MinIO ИЛИ приватный GitHub / filter-repo
- Alertmanager receivers (telegram) · k3s ложные алерты (scheduler/controller-manager)
- Кэш kaniko · registry GC · ResNet-тест реальной картинкой
- Vault: kubernetes-auth для ESO (сейчас 10-летний токен — осознанный компромисс)

---
> Все сервисы: `docs/ACCESS.md` (пароли — локальный ACCESS-local.md, вне git).
> Стек-справочник для повторения: `docs/STACK-GUIDE.md`.
