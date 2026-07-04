# Фаза 2 — Model Serving на NVIDIA Triton (учебный конспект)

> Обучающая станция (k3s, single-node, RTX 5070 Ti). Не prod.
> Цель: развернуть Triton Inference Server и отдавать модели по API.
> Модели: **Iris → ONNX** (связка с нашим pipeline) + **ResNet-50 ONNX** (GPU-демо).
> Статус: 🚧 в работе.

---

## Зачем serving и зачем именно Triton

**Serving** — финальный этап MLOps: обученная модель должна отвечать на запросы (inference) по сети, а не лежать файлом. Клиент шлёт данные → модель возвращает предсказание.

**NVIDIA Triton Inference Server** — почему он:
- Один сервер отдаёт много моделей сразу (multi-model).
- Форматы: ONNX, TensorRT, PyTorch, TensorFlow, Python-backend.
- GPU-инференс + **dynamic batching** (склеивает запросы в батч → выше throughput).
- HTTP и gRPC API из коробки, метрики Prometheus.
- Стандарт для высоконагруженного GPU-serving (в резюме — именно он).

**Почему ONNX** как формат моделей:
- Открытый нейтральный формат. sklearn/PyTorch/TF → ONNX → Triton.
- Не тащим в serving тяжёлый фреймворк обучения — только рантайм.

---

## Термины (для новичка)

| Термин | Простыми словами |
|---|---|
| **Inference** | «прогон» — модель получает вход, отдаёт предсказание |
| **ONNX** | универсальный файл-формат модели, понятный многим рантаймам |
| **Model repository** | папка (или бакет S3) со структурой моделей, откуда Triton их читает |
| **config.pbtxt** | описание модели для Triton: формат, входы/выходы, батчинг |
| **Dynamic batching** | Triton копит запросы и обрабатывает пачкой → эффективнее GPU |

---

## Структура model repository (требование Triton)

```
model_repository/
  iris/
    1/               # версия модели (число)
      model.onnx
    config.pbtxt
  resnet50/
    1/
      model.onnx
    config.pbtxt
```
Правила Triton: `<модель>/<версия>/model.<формат>` + `config.pbtxt` рядом.

---

## Шаги (заполняется по ходу)

### Шаг 1 — Подготовка моделей в ONNX ✅
Конвертеры ставились в отдельный venv (не в проектный):
`scikit-learn skl2onnx onnx onnxruntime boto3`.

- **Iris**: обучили `LogisticRegression`, конвертировали через `skl2onnx.to_onnx` (опция `zipmap=False` — чтобы `probabilities` были обычным тензором, а не списком словарей).
  - Вход: `X` FP32 `[-1, 4]`
  - Выходы: `label` INT64 `[-1]`, `probabilities` FP32 `[-1, 3]`
- **ResNet-50**: скачали готовый `resnet50-v2-7.onnx` (~98 MB) из ONNX model zoo.
  - Вход: `data` FP32 `[N, 3, 224, 224]` (картинка 224×224 RGB)
  - Выход: `resnetv24_dense0_fwd` FP32 `[N, 1000]` (1000 классов ImageNet)

> Как узнали имена входов/выходов: `onnxruntime.InferenceSession(...).get_inputs()/get_outputs()`. Эти имена ОБЯЗАНЫ совпасть с `config.pbtxt`.

### Шаг 4 — config.pbtxt (сделан вместе с шагом 1)
Triton у каждой модели требует `config.pbtxt` — паспорт модели.

**iris** — на CPU (модель крошечная, GPU не нужен):
```
platform: "onnxruntime_onnx"
max_batch_size: 0            # без Triton-батчинга; dims включают batch (-1)
input:  X  FP32 [-1, 4]
output: label INT64 [-1], probabilities FP32 [-1, 3]
instance_group: KIND_CPU
```
**resnet50** — на GPU + dynamic batching (демо мощи Triton):
```
platform: "onnxruntime_onnx"
max_batch_size: 8            # Triton сам добавляет batch-измерение
input:  data FP32 [3, 224, 224]
output: resnetv24_dense0_fwd FP32 [1000]
dynamic_batching { }         # копит запросы в батч → выше throughput на GPU
instance_group: KIND_GPU
```
> `max_batch_size: 0` vs `>0`: при `0` батч-измерение прописываешь сам (`-1` в dims). При `>0` Triton добавляет его автоматически, а в dims пишешь форму ОДНОГО примера.

### Шаг 2 — Залить model repository в MinIO (S3) ✅
Triton умеет читать модели прямо из S3-бакета. Залили структуру в бакет `triton-models` (boto3, endpoint `minio.mlops.local`):
```
triton-models/
  iris/1/model.onnx, iris/config.pbtxt
  resnet50/1/model.onnx, resnet50/config.pbtxt
```

### Шаг 3 — Deploy Triton в кластер (GPU) 🚧
Манифест: `serving/triton.yaml` (Deployment + Service + Ingress + Secret).
Ключевые моменты:
- `image: nvcr.io/nvidia/tritonserver:25.06-py3`
- `runtimeClassName: nvidia` + `resources.limits: nvidia.com/gpu: 1` — даёт поду GPU.
- `--model-repository=s3://http://minio...:9000/triton-models` — S3-репо. Формат `s3://http://<host>:<port>/<bucket>` (префикс `http://` обязателен для MinIO, т.к. не AWS и без TLS).
- Креды MinIO — через Secret `minio-creds` (AWS_ACCESS_KEY_ID/SECRET).
- Порты: 8000 HTTP, 8001 gRPC, 8002 metrics.
- Ingress `triton.mlops.local` → 8000.

> Первый запуск долгий: образ Triton большой (~10-15 GB).

### Шаг 3 — Deploy Triton ✅
Под поднялся (`triton-...` 1/1 Ready). Первый старт ~20 мин: pull образа ~15 GB + загрузка моделей из S3.
Проверка загрузки моделей:
```bash
kubectl exec -n mlops-serving deploy/triton -- curl -s -X POST localhost:8000/v2/repository/index
# [{"name":"iris","version":"1","state":"READY"},{"name":"resnet50","version":"1","state":"READY"}]
```
S3 из MinIO подключился штатно (`Using credential for path s3://http://minio...`).

### Шаг 5 — Тест инференса (HTTP API) ✅
Клиент: `serving/test_infer.py`. Протокол Triton — KServe v2 (`POST /v2/models/<name>/infer`).

**Iris** (2 примера):
```
labels: [0, 2]                    # setosa + virginica — верно
probabilities: [0.982,0.018,0.0,  0.0,0.081,0.919]
```
**ResNet-50** (случайный тензор [1,3,224,224]): вернул 1000 логитов ImageNet, GPU-инференс ответил.

Подтверждение GPU: `instance_group KIND_GPU device[0]`, GPU-память выросла 749→1799 MiB.

**Итог: serving работает. Фаза 2 пункт 2 закрыт.**

---

## Проблемы и решения

### `triton.mlops.local` не резолвится с хоста
Симптом: `curl http://triton.mlops.local/... → Name or service not known`.
Причина: новый домен не добавлен в `/etc/hosts` хоста (другие `*.mlops.local` уже там).
Решение (одно из):
```bash
# постоянно — добавить в /etc/hosts (нужен sudo):
echo '192.168.3.248 triton.mlops.local' | sudo tee -a /etc/hosts
# или временно — port-forward:
kubectl port-forward -n mlops-serving svc/triton 18000:8000
python3 serving/test_infer.py http://localhost:18000
```

### Долг / оптимизации
- Секреты MinIO (`<REDACTED>`) — сейчас в Secret `minio-creds` (лучше, чем в коде, но всё ещё plain). В идеале — External Secrets Operator.
- Метрики Triton (порт 8002) не скрейпятся в VictoriaMetrics — добавить `VMServiceScrape` для `nv_inference_*`.
- Автоматизация: связать с Airflow (после обучения — авто-конвертация в ONNX + заливка в `triton-models` + reload Triton).
- Preprocessing ResNet: сейчас тест шлёт шум. Для реального теста — картинка + ImageNet-нормализация + маппинг классов.
