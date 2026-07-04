# Фаза 3.2 — Миграция mlflow/airflow/minio на ESO-секреты + Argo CD (учебный конспект)

> Дата: 2026-07-04. Продолжение PHASE3-VAULT-ESO.md.
> Итог: пароли ушли из helm-values всех приложений (кроме одного узкого места),
> minio и airflow переведены под Argo CD. Под GitOps теперь: triton, minio, airflow.

## Схема после миграции

```
Vault (secret/mlops/{minio, mlflow-postgres})
  └─ ESO ClusterSecretStore "vault"
       ├─ minio-root            (ns mlops-infra)    -> chart minio: existingSecret
       ├─ mlflow-s3-creds       (ns mlops-tracking) -> chart mlflow: artifactRoot.s3.existingSecret
       ├─ mlflow-db-creds       (ns mlops-tracking) -> ЗАГОТОВКА (см. «Узкое место»)
       ├─ airflow-minio-creds   (ns mlops-pipelines)-> chart airflow: secret[] -> env task-подов
       └─ minio-creds           (ns mlops-serving)  -> triton envFrom (сделано в 3.1)
```

## Как каждый чарт потребляет секреты (3 разных паттерна!)

| Чарт | Механизм | values |
|---|---|---|
| minio 5.4.0 | `existingSecret: minio-root` (ключи rootUser/rootPassword) | `minio-values.yaml` |
| mlflow 1.8.1 | `artifactRoot.s3.existingSecret` (имя + имена ключей) | `mlflow-values.yaml` |
| airflow 1.21.0 | `secret:` список → env из secretKeyRef во все поды (включая task-поды KubernetesExecutor) | `airflow-values.yaml` |

> Урок: у каждого чарта свой способ подключить существующий Secret — всегда смотреть
> `helm show values <chart>` на `existingSecret` / `secret:` / `extraEnvFrom` ДО миграции.

## Узкое место: mlflow backendStore (осознанный компромисс)

Чарт mlflow **1.8.1** НЕ умеет existingSecret для `backendStore.postgres`
(schema: `additional properties 'existingSecret' not allowed`) — пароль обязан быть в values.
Поддержка появилась только в чарте **1.11.x** (app MLflow 3.14, наш — 3.7 → нужна миграция БД).

Решение на сегодня:
- S3-креды mlflow — из ESO (умеет и 1.8.1) ✅
- DB-пароль — остаётся в `mlflow-values.yaml`, файл в `.gitignore` (в git — `mlflow-values.example.yaml`)
- ExternalSecret `mlflow-db-creds` уже создан — заготовка
- TODO следующей сессии: chart upgrade → 1.11.2 (+`databaseMigration: true`) → пароль из ESO → mlflow в Argo

## Argo CD: multi-source Application (новый паттерн)

Для helm-приложений один source не может взять chart из одного места, values из другого.
Multi-source может (`gitops/apps/minio.yaml`, `gitops/apps/airflow.yaml`):
```yaml
sources:
  - repoURL: https://charts.min.io/          # chart из helm-репо
    chart: minio
    targetRevision: 5.4.0
    helm:
      releaseName: minio                     # = имя существующего релиза -> adoption без пересоздания
      valueFiles: [$values/minio-values.yaml]
  - repoURL: https://github.com/bestpracticeRM-RF/ai-study.git
    targetRevision: main
    ref: values                              # $values = корень нашего репо
```
Осторожность со stateful:
- `prune: false` — Argo ничего не удаляет сам (Postgres/PVC).
- airflow: `selfHeal: false` на период наблюдения + ignoreDifferences на Secret
  (чарт генерит ключи при установке — иначе вечный OutOfSync).

⚠️ После adoption ручной `helm upgrade` для этих релизов больше НЕ делаем —
источник правды git, изменения только через commit+push.

## Проверки (все прошли)

- minio: rollout ok, `MINIO_ROOT_USER` из ESO-секрета, health 200
- mlflow: rev 2, `AWS_*` env из `mlflow-s3-creds`, эксперименты целы
- airflow: rev 3, env scheduler/task-подов из `airflow-minio-creds`
- **E2E**: DAG `iris_training_pipeline` — все 3 task success, новый run в MLflow (accuracy=1.0),
  т.е. креды из Vault реально доехали до task-пода и до S3
- Argo: `airflow Synced/Healthy`, `minio Synced/Healthy`, `triton Synced/Healthy`

## Статус GitOps-покрытия

| Релиз | Секреты из ESO | Под Argo |
|---|---|---|
| triton | ✅ | ✅ |
| minio | ✅ | ✅ |
| airflow | ✅ | ✅ |
| mlflow | S3 ✅ / DB ⏳ | ⏳ (после chart upgrade) |
| vault, ESO, vm-stack, grafana, ingress, cert-manager, argocd, postgresql | — | ⏳ (по мере надобности) |

## Долг (перенесено на следующие сессии)
1. mlflow chart 1.11.2: `databaseMigration: true`, DB-пароль из ESO, в Argo.
2. Ротация пароля MinIO (старый засвечен в git-истории) — теперь одна команда `vault kv put` + рестарты.
3. ESO: kubernetes-auth вместо статичного токена (TTL 32 дня!).
4. Vault: runbook на unseal после рестарта (ключ в `~/.config/vault-lab-keys.json`).
5. Argo: app-of-apps паттерн (одно корневое Application вместо ручных kubectl apply).
