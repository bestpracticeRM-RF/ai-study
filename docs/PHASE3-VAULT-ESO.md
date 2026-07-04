# Фаза 3.1 — Секреты: Vault + External Secrets Operator (учебный конспект)

> Дата: 2026-07-04. Обучающая станция. Первый пункт Фазы 3.
> Итог: пароли уходят из git/values. Vault хранит, ESO доставляет в k8s Secrets.

## Зачем и почему связка (не «или»)

- **Vault** — хранилище секретов: шифрование, версии, политики доступа, аудит.
- **ESO (External Secrets Operator)** — доставщик: читает Vault и создаёт обычные
  k8s Secrets, которые ждут helm-чарты и поды (`envFrom: secretRef`).
- Vault без ESO: приложения должны сами ходить в Vault (сайдкары/код) — чарты этого не умеют.
- ESO без Vault: нужен другой backend (Yandex Lockbox, AWS SM...). Для on-prem РФ Vault — стандарт.

Поток: `Vault (secret/mlops/minio) -> ESO -> k8s Secret minio-creds -> под Triton`.

## Что развёрнуто

### 1. Vault (ns `vault`, chart hashicorp/vault 0.34.0, app 2.0.3)
`vault-values.yaml` (без секретов, в git):
- **standalone** режим + PVC 4Gi (`local-path`) — данные переживают рестарт.
  НЕ dev-режим (там всё в памяти и root-token фиксированный).
- TLS выключен (лаба!), UI включен, ingress `vault.mlops.local`.
- injector выключен — доставка через ESO, сайдкары не нужны.

### 2. Инициализация (одноразовые ручные шаги)
```bash
kubectl exec -n vault vault-0 -- vault operator init -key-shares=1 -key-threshold=1 -format=json \
  > ~/.config/vault-lab-keys.json     # ключи ВНЕ репо! chmod 600
kubectl exec -n vault vault-0 -- vault operator unseal <unseal_key_b64>
```
> ⚠️ После КАЖДОГО рестарта пода Vault — снова `unseal` (ключ в `~/.config/vault-lab-keys.json`).
> В prod это решает auto-unseal (KMS) или несколько key-shares у разных людей.

### 3. Секрет + доступ для ESO (принцип least privilege)
```bash
vault secrets enable -path=secret kv-v2
vault kv put secret/mlops/minio access_key=... secret_key=...
vault policy write eso-read -   # read-only на secret/data/mlops/*
vault token create -policy=eso-read -ttl=768h
```
ESO ходит НЕ root-токеном, а токеном с политикой только на чтение `mlops/*`.

### 4. ESO (ns `external-secrets`, chart external-secrets)
`gitops/eso/vault-store.yaml` (в git, секретов нет):
- **ClusterSecretStore `vault`** — как подключаться (адрес svc, kv-v2, где лежит токен).
- **ExternalSecret `minio-creds`** (ns mlops-serving) — какой секрет собрать:
  из `secret/mlops/minio` → k8s Secret с ключами AWS_ACCESS_KEY_ID/SECRET/REGION,
  `refreshInterval: 1h` (ротация в Vault доедет сама), `creationPolicy: Owner`.
- Токен для ESO — единственный ручной секрет:
  `kubectl create secret generic vault-eso-token -n external-secrets --from-literal=token=...`

### 5. Миграция Triton
Старый рукотворный `minio-creds` удалён → ESO пересоздал его из Vault (SecretSynced).
`rollout restart` Triton — под перечитал секрет, модели снова READY.

## Проверка здоровья
```bash
kubectl exec -n vault vault-0 -- vault status            # Sealed: false
kubectl get clustersecretstore vault                     # Valid
kubectl get externalsecret -A                            # SecretSynced
kubectl get secret minio-creds -n mlops-serving          # существует, 3 ключа
```

## Что это меняет (архитектурно)
1. В git больше не нужны пароли ни в каком виде — только ExternalSecret-манифесты (безопасны).
2. Разблокирован полный GitOps: helm-values теперь могут ссылаться на существующие
   k8s Secrets (`existingSecret`), которые создаёт ESO → можно переводить mlflow/airflow/minio в Argo CD.
3. Смена пароля = `vault kv put ...` → ESO обновит Secret за ≤1h (или мгновенно при пересоздании ExternalSecret).

## Долг / следующие шаги
- Перевести mlflow/airflow/minio на секреты из ESO (убрать пароли из values) → потом в Argo CD.
- Сменить пароль MinIO (`minioadmin123` засвечен в публичной git-истории) — теперь это просто.
- Kubernetes-auth для ESO вместо статичного токена (по TTL истечёт через 32 дня).
- Auto-unseal или задокументированный runbook на рестарт Vault.
