# Фаза 3.5 — Registry + CI-сборка train-образа + быстрый DAG (учебный конспект)

> Дата: 2026-07-05. Итог: полный цикл «commit → CI собирает образ → registry →
> Airflow DAG в этом образе». **Время run: ~12 мин → ~85 сек.**

## Зачем

DAG v1 (`iris_training_pipeline`) на каждый run ставил mlflow+sklearn в venv —
10-12 минут чистого pip. Правильно: собрать образ один раз в CI, task-поды его
переиспользуют. Это стандартная прод-практика.

## Цепочка

```
git push gitlab ──► pipeline: lint → build:train (kaniko)
                                        │ push (svc-адрес, --insecure)
                                        ▼
                    registry:2 (ns gitlab, PVC 20Gi, registry.mlops.local)
                                        │ pull нодой (registries.yaml k3s)
                                        ▼
Airflow DAG iris_training_fast: @task(executor_config=pod_override(image=train))
                                        │
                                        ▼  ~85 сек end-to-end
                    MLflow: эксперимент Airflow_Iris_Fast, accuracy=1.0
```

## Ключевые решения

### Registry в кластере (`gitops/platform/registry.yaml`)
`registry:2` + PVC + ingress `registry.mlops.local` (`proxy-body-size: 0` — слои большие).
HTTP без TLS — лаба.

### Два имени одного registry (важно понять!)
- **kaniko пушит** на `registry.gitlab.svc.cluster.local:5000` — под НЕ резолвит
  `*.mlops.local` (это hosts-домены хоста).
- **нода тянет** `registry.mlops.local/...` — containerd работает на хосте,
  резолвит через /etc/hosts, а HTTP разрешён через registries.yaml.
Путь репозитория (`mlops/train`) одинаков — хранилище одно.

### k3s registries.yaml (+ restart k3s)
```yaml
# /etc/rancher/k3s/registries.yaml
mirrors:
  "registry.mlops.local":
    endpoint:
      - "http://registry.mlops.local"
```
k3s генерит containerd hosts.toml. Рестарт k3s: запущенные контейнеры живут
(шимы отдельно от k3s-процесса), API моргает ~15 сек, Vault unseal НЕ потребовался.

### kaniko (не docker build)
CI-джоба в поде — docker-демона нет. kaniko собирает образ в user-space:
`gcr.io/kaniko-project/executor:debug` + `--insecure` для HTTP-push.

### pod_override вместо KubernetesPodOperator
```python
@task(executor_config={"pod_override": k8s.V1Pod(spec=k8s.V1PodSpec(
    containers=[k8s.V1Container(name="base", image=TRAIN_IMAGE,
                                image_pull_policy="Always")]))})
```
Обычный @task, но task-под стартует из нашего образа. Импорты «просто работают».

## 🪤 Грабли (4 шт — маршрут построения любого airflow-образа)

1. **`ModuleNotFoundError: psycopg2`** — task-под инициализирует ORM по env
   `AIRFLOW__DATABASE__*` → нужен postgres-драйвер в образе.
2. **`ModuleNotFoundError: asyncpg`** — Airflow 3 использует и async-SQLAlchemy.
3. **`getpwuid(): uid not found: 50000`** — чарт запускает поды под uid 50000
   (юзер официального образа), в `python:slim` его нет.
   → **Правильное решение всей цепочки: `FROM apache/airflow:3.2.0`** + только
   ML-зависимости сверху. Совпадают uid, entrypoint, версии airflow/драйверов.
4. **`:latest` + `imagePullPolicy: IfNotPresent` = протухший кэш** — DAG молча
   запускал СТАРЫЙ образ после пересборки. Фиксы: `image_pull_policy="Always"`
   (лаба) или пин по SHA/digest (prod). Отдельная подлость: SHA-пин нельзя
   положить в тот же коммит, который собирает образ (hash ещё неизвестен).

## Сравнение v1 vs v2 (одна и та же задача)

| | v1 `iris_training_pipeline` | v2 `iris_training_fast` |
|---|---|---|
| Механизм | `@task.virtualenv`, pip в run | готовый образ, pod_override |
| Время run | ~12 мин | **~85 сек** |
| Сеть/PyPI в рантайме | каждый раз | нет |
| Воспроизводимость | зависит от PyPI на момент run | образ immutable |

## Что дальше (задел)
- Deploy-стадия CI: bump тега в values → Argo — полный GitOps-цикл релиза модели.
- Kaniko cache (--cache=true + repo) — ускорить пересборки.
- Registry GC/retention — PVC 20Gi не резиновый.
- Перевести drift-DAG на этот же образ (evidently уже внутри).
