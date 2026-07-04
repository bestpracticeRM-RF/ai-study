# Доступы к сервисам платформы (браузер)

> Один раз добавь недостающие домены в /etc/hosts:
> ```bash
> echo '192.168.3.248 vault.mlops.local triton.mlops.local console.minio.mlops.local' | sudo tee -a /etc/hosts
> ```
> (mlflow/minio/airflow/grafana/argocd уже прописаны)

| Сервис | URL | Логин / пароль | Что смотреть/учить |
|---|---|---|---|
| **MLflow** | http://mlflow.mlops.local | без логина | эксперименты, runs, метрики, артефакты моделей |
| **Airflow** | http://airflow.mlops.local | `admin` / из Vault (secret/mlops/airflow) | DAG-и, Grid view, логи задач, триггер руками |
| **Grafana** | http://grafana.mlops.local | `admin` / см. ниже | дашборд «GPU / MLOps (as-code)», Explore+PromQL |
| **Argo CD** | http://argocd.mlops.local | `admin` / см. ниже | приложения, Synced/OutOfSync, diff с git, Sync |
| **Vault UI** | http://vault.mlops.local | Token: root-токен, см. ниже | Secrets Engines → `secret/` → `mlops/` |
| **MinIO Console** | http://console.minio.mlops.local | root-креды: см. ниже | бакеты `mlflow-artifacts`, `triton-models` |
| **Triton** | http://triton.mlops.local | API без UI | `/v2/health/ready`, `/v2/models/iris/config` |

## Команды для паролей (не хранить в файлах!)

```bash
# Grafana admin
kubectl get secret -n monitoring vm-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo

# Argo CD admin
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo


# Vault root-токен (лаба; в prod root-токен не используют)
python3 -c "import json;print(json.load(open('/home/btk/.config/vault-lab-keys.json'))['root_token'])"

# MinIO root (из Vault — единственный источник правды)
kubectl exec -n vault vault-0 -- sh -c 'VAULT_TOKEN=<root-токен> vault kv get -format=json secret/mlops/minio' | python3 -m json.tool
# или проще — из k8s Secret, который сделал ESO:
kubectl get secret -n mlops-infra minio-root -o jsonpath='{.data.rootPassword}' | base64 -d; echo
```

## Где смотреть СЕКРЕТЫ (Vault UI)

1. http://vault.mlops.local → Method: **Token** → вставить root-токен
2. **Secrets Engines** → `secret/` → папка **mlops/**:
   - `minio` — access_key / secret_key (MinIO root)
   - `mlflow-postgres` — username / password (БД MLflow)
3. Каждый секрет: версии (kv-v2), кнопка редактирования — поменяешь тут → ESO
   разнесёт по кластеру за ≤1h (или мгновенно: `kubectl annotate externalsecret ... force-sync=$(date +%s)`)

Куда ESO их доставляет (k8s Secrets): `kubectl get externalsecret -A`

## Vault: рестарт пода = ручной unseal!
```bash
kubectl exec -n vault vault-0 -- vault operator unseal \
  $(python3 -c "import json;print(json.load(open('/home/btk/.config/vault-lab-keys.json'))['unseal_keys_b64'][0])")
```

## Мини-путеводитель «поучаствовать»

1. **Смотреть пайплайн**: Airflow UI → `iris_training_pipeline` → Trigger → смотри Grid.
2. **Смотреть результат**: MLflow → эксперимент `Airflow_Iris_Pipeline` → новый run.
3. **Смотреть GPU под нагрузкой**: Grafana → GPU/MLOps дашборд, затем дай нагрузку:
   `python3 serving/test_infer.py http://triton.mlops.local`
4. **GitOps руками**: поменяй что-то в `serving/triton.yaml` (например `--log-verbose=0`),
   commit+push → Argo CD UI → смотри как приедет само.
5. **Секреты**: Vault UI → поменяй secret_key MinIO → смотри как ESO обновит
   `kubectl get secret -n mlops-infra minio-root -o yaml` (после refresh).

---

## Быстрый доступ (пароли)

Реальные пароли — в **`docs/ACCESS-local.md`** (локальный файл, в `.gitignore`, в репо не попадает).
Если файла нет/устарел — перегенерировать командами из раздела «Команды для паролей» выше.
