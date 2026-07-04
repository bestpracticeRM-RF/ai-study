# Фаза 2 — GitOps: Argo CD управляет Triton (учебный конспект)

> Дата: 2026-07-04. Обучающая станция. Закрывает пункт 3 (последний) Фазы 2.
> Итог: Argo CD синкает `serving/` из GitHub в кластер. Ручной `kubectl apply` для Triton больше не нужен.

## Что такое GitOps (просто)

Git = единственный источник правды. В кластер руками не ходим:
1. правишь манифест в репо → `git push`
2. Argo CD видит новый коммит → сам применяет в кластер
3. откат = `git revert`; ручной дрейф в кластере Argo откатывает обратно (selfHeal)

## Что сделано

### 1. Application-манифест: `gitops/apps/triton.yaml`
```yaml
source:
  repoURL: https://github.com/bestpracticeRM-RF/ai-study.git
  targetRevision: main
  path: serving
  directory:
    include: "triton.yaml"   # в serving/ лежат и .py/.md — берём только манифест
destination:
  namespace: mlops-serving
syncPolicy:
  automated:
    prune: true      # удалил из git → удалится из кластера
    selfHeal: true   # kubectl-дрейф откатывается к git
```

### 2. Bootstrap (единственный ручной шаг, один раз)
```bash
kubectl apply -f gitops/apps/triton.yaml
```
Дальше Argo работает сам.

### 3. Результат
```
sync=Synced health=Healthy
Namespace/mlops-serving Synced
Service/triton Synced
Deployment/triton Synced
Ingress/triton Synced
```
**Adoption прошёл бесшовно:** Triton уже был задеплоен вручную — Argo сравнил живые ресурсы с git-манифестами (идентичны) и взял их под управление БЕЗ пересоздания пода (инференс не прерывался).

## Рабочий цикл теперь

```bash
vim serving/triton.yaml     # правка (например, image tag)
git commit && git push      # единственное действие
# Argo применит сам (poll ~3 мин; можно вручную: Sync в UI)
```
Проверка: `kubectl get application -n argocd triton` → `Synced Healthy`.

## UI Argo CD
```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
# https://localhost:8080, логин admin, пароль:
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

## Почему пока только Triton (важное решение)

Helm-релизы (mlflow, airflow, minio...) в Argo НЕ переведены осознанно:
их values содержат пароли (Postgres, MinIO). GitOps требует values в Git,
а класть секреты в Git нельзя (уже обожглись — см. «Уроки безопасности» ниже).

**Правильный порядок:** сначала External Secrets Operator (Фаза 3) → секреты
уедут из values в ESO → тогда helm-приложения переводятся в Argo через
multi-source Application (chart из helm-repo + values из git). Паттерн:
```yaml
sources:
  - repoURL: https://community-charts.github.io/helm-charts
    chart: mlflow
    targetRevision: 1.8.1
    helm:
      valueFiles: [$values/mlflow-values.yaml]
  - repoURL: https://github.com/bestpracticeRM-RF/ai-study.git
    targetRevision: main
    ref: values
```

## Уроки безопасности (реальные грабли этой сессии)

1. **Пуш кредов в публичный репо уже случился раньше** (коммит `change oll`:
   `test_mlflow.py`, `airflow-values.yaml`, старый `mlflow-values.yaml` — пароли лабы
   в открытой истории GitHub). Автоклассификатор заблокировал мой повторный пуш — и по делу.
2. Санитизация в этом коммите:
   - `mlflow-values.yaml` убран из git (`git rm --cached` + .gitignore), вместо него `mlflow-values.example.yaml` с плейсхолдерами;
   - Secret удалён из `serving/triton.yaml` — создаётся императивно: `kubectl create secret generic minio-creds ...`;
   - `prepare_models.py` требует креды через env (не хранит дефолтов);
   - пароли в доках замаскированы `<REDACTED>`.
3. **Остаточный долг:** старая история репо всё ещё содержит пароли. Варианты:
   сделать репо приватным ИЛИ переписать историю (`git filter-repo`) + сменить пароли MinIO/Postgres.
   Для LAN-only лабы некритично, но для портфолио-репо — сделать.

## Статус Фазы 2: ЗАКРЫТА ✅

| Пункт | Статус | Докой |
|---|---|---|
| Airflow DAG (Iris→MLflow) | ✅ | PHASE2-AIRFLOW-DAG.md |
| Model Serving (Triton, GPU) | ✅ | PHASE2-TRITON-SERVING.md |
| GitOps (Argo CD ← Git) | ✅ | этот файл |

## Следующее — Фаза 3 (кандидаты по приоритету)
1. **External Secrets Operator** — секреты из Git/values → разблокирует полный GitOps
2. **Evidently** — мониторинг дрейфа модели → метрики в VictoriaMetrics
3. **DVC** — версионирование датасетов в MinIO
4. **Свой Docker-образ для обучения** — убрать 10-мин venv в DAG (KubernetesPodOperator)
