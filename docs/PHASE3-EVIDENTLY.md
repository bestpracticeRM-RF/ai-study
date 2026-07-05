# Фаза 3.3 — Мониторинг дрейфа: Evidently → VictoriaMetrics → алерты (учебный конспект)

> Дата: 2026-07-05. Итог: рабочая цепочка drift-мониторинга с алертом.
> `DAG (evidently) → метрики в vmsingle → VMRule → vmalert: IrisDatasetDrift firing` ✅

## Что такое дрейф и зачем его мониторить

Модель обучена на одних данных, а в прод со временем приходят другие
(сезонность, новые пользователи, поломка источника). Accuracy на проде не
посчитать сразу (нет ground truth), а вот **сравнить распределения** входных
данных можно немедленно. Это и есть data drift detection:

- **reference** — данные обучения (эталон)
- **current** — свежие прод-данные
- по каждой фиче — статтест (K-S, p-value): распределение то же?
- дрейфнуло достаточно фич → сигнал «модель, вероятно, деградировала → переобучить»

Grafana/DCGM следят за железом, Evidently — за **самой моделью**. Это разные слои.

## Архитектура (всё нашими паттернами)

```
Airflow DAG iris_drift_monitoring (gitSync)
  └─ drift_report (@task.virtualenv: evidently 0.7)
       reference=Iris train  vs  current=сэмпл (+опц. инжект дрейфа)
       │  POST plain-text метрики
       ▼
vmsingle :8428/api/v1/import/prometheus   ← пуш без Pushgateway, нативно VM
       ├─► vmalert (VMRule model-drift) ─► алерт IrisDatasetDrift
       └─► Grafana дашборд "Model Monitoring" (ConfigMap as-code)
```

Файлы:
- `dags/iris_drift_monitoring.py` — DAG
- `gitops/monitoring/drift-alerts.yaml` — VMRule (+ GpuHot бонусом)
- `gitops/monitoring/model-monitoring-dashboard.yaml` — дашборд

## Метрики

| Метрика | Смысл |
|---|---|
| `evidently_dataset_drift{dataset="iris"}` | 0/1 — датасет дрейфнул (share ≥ 0.5) |
| `evidently_drift_share` | доля дрейфнувших фич |
| `evidently_drifted_features` | счётчик |
| `evidently_feature_drift_score{feature=}` | **p-value** K-S теста (мало ⇒ дрейф!) |

## Как запускать

```bash
# без дрейфа (здоровый прогон)
airflow dags trigger iris_drift_monitoring
# с искусственным дрейфом (демо алерта): сдвигаются 2 фичи из 4
airflow dags trigger iris_drift_monitoring --conf '{"inject_drift": true}'
```
Результат: Grafana → «Model Monitoring — Evidently», алерты → vmalert UI / панель ALERTS.

Проверенный итог демо: `dataset_drift=1, share=0.5`, p-value сдвинутых фич ≈ 0,
алерт `IrisDatasetDrift` в состоянии **firing**.

## 🪤 Грабли (реальные, три штуки — ценность конспекта)

### 1. numpy без wheels под Python 3.13
`evidently==0.6.7` → резолвер берёт `numpy 2.0.2` → у него НЕТ wheel под py3.13
(первые cp313-wheels — numpy 2.1) → uv собирает sdist → в airflow-образе нет gcc:
```
../meson.build:1:0: ERROR: Unknown compiler(s): [['cc'], ['gcc']]
```
> Урок: в контейнерных venv всегда проверяй наличие wheels под python образа.

### 2. Конфликт версий
Пин `numpy>=2.1` не помог:
```
Because evidently==0.6.7 depends on numpy>=1.22.0,<2.1 and you require numpy>=2.1 → no solution
```
Единственный выход — evidently 0.7+ (новый API).

### 3. Формат dict в evidently 0.7 совсем другой
Старый API: `report.as_dict()["metrics"][i]["metric"]=="DatasetDriftMetric"`.
Новый (0.7.21): у метрики ключ **`metric_name`** (`"DriftedColumnsCount(drift_share=0.5)"`,
`"ValueDrift(column=...)"`), имя колонки — чисто в `config.column`,
`ValueDrift.value` = p-value.
> Урок: не гадать по памяти о формате библиотеки — поставить локально в venv,
> напечатать реальный dict, писать парсер по факту. Сэкономило N циклов по 4 мин
> (каждый цикл DAG = venv-установка evidently заново).

## Ограничения демо (честно)
- current-данные синтетические (сэмпл reference + шум/сдвиг). В реале — собирать
  входы инференса (логирование запросов Triton) и сравнивать окно за час/день.
- Метрика живёт «точкой» — между запусками DAG instant-запрос может вернуть пусто
  (staleness ~5 мин). Для постоянного мониторинга включить `schedule="@hourly"`.
- venv ставится ~3-4 мин на каждый run → свой Docker-образ с evidently (долг).
- Алерты горят в vmalert, но receivers (telegram/email) не настроены.

## Известный шум алертов на k3s
`KubeControllerManagerDown`, `KubeSchedulerDown` — ложные: в k3s эти компоненты
встроены в один бинарь и не отдают отдельные scrape-таргеты. Стандартные правила
kube-prometheus их «теряют». Лечится отключением этих правил в values vm-stack (долг-мелочь).
