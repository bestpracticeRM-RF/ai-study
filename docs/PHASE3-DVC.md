# Фаза 3.6 — DVC: версионирование датасетов в MinIO (учебный конспект)

> Дата: 2026-07-05. Итог: датасет под версионным контролем, хранилище — MinIO,
> продемонстрирован откат между версиями. Последний крупный пункт плана закрыт.

## Идея DVC (просто)

Git не для больших/бинарных данных (история распухает). DVC разделяет:
- **в git** — крошечный `.dvc`-файл (md5 + размер): «указатель» на версию данных
- **в S3 (MinIO)** — сами данные, адресуемые хэшем (content-addressable)

Версия данных = версия `.dvc`-файла в git. Откат данных = `git checkout` + `dvc checkout`.

## Настройка (сделано)

```bash
pipx install 'dvc[s3]'
dvc init
dvc remote add -d minio s3://dvc-store
dvc remote modify minio endpointurl http://minio.mlops.local
# креды — ТОЛЬКО в .dvc/config.local (dvc сам держит его вне git):
dvc remote modify --local minio access_key_id <...>
dvc remote modify --local minio secret_access_key <...>
```
`.dvc/config` (без секретов) — в git; `.dvc/config.local` — авто-gitignored ✓.

## Рабочий цикл

```bash
# новая версия данных:
vim data/iris.csv                 # (или пайплайн перегенерил)
dvc add data/iris.csv             # пересчитал md5, обновил iris.csv.dvc
git commit -am "dataset v2"       # версия зафиксирована в истории git
dvc push                          # данные уехали в MinIO

# откат к любой версии:
git checkout <commit> -- data/iris.csv.dvc
dvc checkout                      # файл данных заменён на ту версию
```

## Что продемонстрировано (проверено)

| Шаг | Результат |
|---|---|
| v1: iris 150 строк → `dvc add` + `push` | md5 `21d441a2`, 1 file pushed |
| v2: +20 «новых измерений» → `add`+`push` | вторая версия в MinIO |
| Откат: `git checkout HEAD~1 -- *.dvc` + `dvc checkout` | файл снова 150 строк |
| Возврат на v2 | 170 строк |
| MinIO bucket `dvc-store` | 2 объекта (обе версии, дедуп по хэшу) |

## Как это стыкуется с остальной платформой

- **Данные** лежат рядом с артефактами моделей (MLflow) и образами (registry) — всё в MinIO.
- **Reference-датасет для Evidently**: дрейф-DAG может брать эталон конкретной версии
  (`dvc get`/`dvc.api.read()` по ревизии git) — воспроизводимое сравнение.
- **Обучение**: train-DAG может пиниться на версию данных так же, как на SHA образа —
  полная воспроизводимость: код (git) + данные (dvc) + окружение (образ).

## Ограничения/долг
- `dvc pull` в DAG-ах пока не используется (датасет игрушечный, грузится из sklearn).
  При реальных данных: `dvc[s3]` уже в train-образ + `dvc pull` первым шагом task.
- Секреты MinIO в `.dvc/config.local` руками — при желании генерить из Vault.
