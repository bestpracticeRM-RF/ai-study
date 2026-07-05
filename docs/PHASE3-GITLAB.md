# Фаза 3.4 — GitLab CI/CD в кластере (учебный конспект)

> Дата: 2026-07-05. Итог: self-hosted GitLab + runner в k3s, первый пайплайн зелёный.
> `git push gitlab main → pipeline (lint:dags, lint:yaml) → success` ✅

## Архитектура

```
ns gitlab:
  gitlab (CE omnibus, 1 под, ~4GB)  ← gitlab.mlops.local, root-пароль из Vault
  gitlab-runner (kubernetes executor) ← CI-джобы = отдельные поды в ns gitlab
Репо-зеркало: GitHub (origin, gitSync/Argo) + GitLab (ci)
```

## Решение «где GitLab» (обсуждено)
- Полный cloud-native chart: ~15 подов, 8GB+ — не влез бы рядом с Jupyter GPU (свободно было ~11GB).
- gitlab.com + runner: легко, но «self-hosted GitLab» в резюме не появился бы.
- **Выбрано: CE omnibus одним Deployment (~4GB)** — реальный опыт админства, влезает.
  В prod так не делают (нет HA) — компромисс лабы, проговорён.

## Ключевые куски конфига

**Omnibus (env GITLAB_OMNIBUS_CONFIG):**
```ruby
external_url 'http://gitlab.mlops.local'
gitlab_rails['monitoring_whitelist'] = ['127.0.0.0/8', '10.42.0.0/16', '10.43.0.0/16']
prometheus_monitoring['enable'] = false   # метрики собирает наш VM-стек
registry['enable'] = false                # registry отдельно (следующий шаг)
sidekiq['max_concurrency'] = 5            # экономия RAM
puma['worker_processes'] = 2
```
Root-пароль: env `GITLAB_ROOT_PASSWORD` ← ESO-секрет `gitlab-root` ← Vault `secret/mlops/gitlab`.

**Runner (`gitlab-runner-values.yaml`):** kubernetes executor; и `url`, и `clone_url`
на **внутрикластерный svc** `http://gitlab.gitlab.svc.cluster.local` — потому что
`gitlab.mlops.local` живёт только в /etc/hosts хоста, поды его не резолвят.

**Регистрация runner (GitLab 16+ flow):** не registration-token, а
`POST /api/v4/user/runners` (с PAT) → `glrt-...` токен → k8s Secret → chart.

## 🪤 Грабли (две, обе пойманы)

### 1. GitLab 18 удалил omnibus-ключ `grafana`
```
Mixlib::Config::UnknownConfigOptionError: Reading unsupported config value grafana.
```
Grafana была встроена для self-мониторинга; выпилена (смена лицензии Grafana на AGPL
в 2021 + груз сопровождения; deprecated с 16, удалена в 18). Конфиг omnibus строгий:
неизвестный ключ = fatal, не warning → CrashLoop.
> Урок: перед мажорным апгрейдом читать removals-changelog — старый конфиг убивает новую версию.

### 2. Health-эндпоинты за IP-whitelist
Под «вечно 0/1», проба: `Readiness probe failed: statuscode: 404`.
GitLab отдаёт `/-/readiness` только whitelisted-IP (дефолт 127.0.0.0/8),
а kubelet ходит с IP ноды → 404 → NotReady → ingress 503.
Фикс: `monitoring_whitelist` + pod/svc CIDR (10.42/16, 10.43/16).

## Первый пайплайн (`.gitlab-ci.yml`)
```yaml
stages: [lint]
lint:dags:  # py_compile всех DAG-ов
lint:yaml:  # yaml.safe_load_all всех манифестов/values
```
Прогон: оба job → success. CI-джобы бегут подами в ns gitlab (executor kubernetes).

## Рабочий цикл (двухремотный, временно)
```bash
git push origin main   # GitHub: gitSync (Airflow DAGs) + Argo CD (манифесты)
git push gitlab main   # GitLab: CI-пайплайн
```
Позже можно: GitLab = единственный origin, GitHub — зеркало (или Argo переключить на GitLab).

## Доступ
- UI: http://gitlab.mlops.local (root / Vault `secret/mlops/gitlab`)
- В Vault же: `automation_pat` (API), `runner_token`

## Следующие шаги CI (по плану)
1. **Registry** в кластере + `registries.yaml` k3s (insecure pull) — рестарт k3s, аккуратно.
2. **Build-стадия**: kaniko собирает train-образ (mlflow+sklearn+evidently) → registry.
3. Airflow DAG на `KubernetesPodOperator` с этим образом → venv-боль (10 мин/run) умирает.
4. Deploy-стадия: bump тега в values → Argo подхватывает (полный GitOps-цикл).
