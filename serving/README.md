# serving/ — Triton Inference Server

Разворачивает NVIDIA Triton и отдаёт 2 модели: **iris** (CPU) и **resnet50** (GPU).
Подробный учебный разбор — в [../docs/PHASE2-TRITON-SERVING.md](../docs/PHASE2-TRITON-SERVING.md).

## Файлы
| Файл | Что |
|---|---|
| `triton.yaml` | манифест: Namespace + Secret + Deployment(GPU) + Service + Ingress |
| `model_repository/*/config.pbtxt` | конфиги моделей для Triton (коммитятся) |
| `prepare_models.py` | генерит Iris ONNX, качает ResNet-50, заливает всё в MinIO |
| `test_infer.py` | тест инференса (KServe v2) для обеих моделей |
| `model_repository/*/1/model.onnx` | сами модели — НЕ в git, регенерируются `prepare_models.py` |

## Развернуть с нуля
```bash
# 1. подготовить и залить модели в MinIO (нужен venv с зависимостями — см. шапку prepare_models.py)
python3 serving/prepare_models.py

# 2. развернуть Triton
kubectl apply -f serving/triton.yaml

# 3. дождаться готовности (первый старт ~20 мин: pull образа + загрузка моделей)
kubectl get pods -n mlops-serving -w

# 4. тест
kubectl port-forward -n mlops-serving svc/triton 18000:8000 &
python3 serving/test_infer.py http://localhost:18000
```

## Доступ через домен
Добавить в `/etc/hosts` (один раз):
```bash
echo '192.168.3.248 triton.mlops.local' | sudo tee -a /etc/hosts
```
Тогда: `curl http://triton.mlops.local/v2/health/ready`.

## Полезные эндпоинты (Triton KServe v2)
```
GET  /v2/health/ready              # готов
POST /v2/repository/index          # статус моделей (READY/UNAVAILABLE)
GET  /v2/models/<name>/config      # конфиг модели
POST /v2/models/<name>/infer       # инференс
GET  /metrics (порт 8002)          # Prometheus-метрики
```
