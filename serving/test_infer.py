#!/usr/bin/env python3
"""
Тест инференса Triton (HTTP v2 / KServe protocol).
  - iris:     реальный инференс на 2 примерах (setosa + virginica)
  - resnet50: случайный тензор нужной формы -> проверяем, что GPU-модель отвечает

Запуск:
  python3 serving/test_infer.py [BASE_URL]
  BASE_URL по умолчанию http://triton.mlops.local
"""
import sys, json, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://triton.mlops.local"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return json.load(r)


def iris_test():
    print("== iris ==")
    # 2 примера: явная setosa и явная virginica
    data = [[5.1, 3.5, 1.4, 0.2], [6.7, 3.0, 5.2, 2.3]]
    payload = {"inputs": [
        {"name": "X", "shape": [2, 4], "datatype": "FP32",
         "data": [v for row in data for v in row]},
    ]}
    out = post("/v2/models/iris/infer", payload)
    res = {o["name"]: o["data"] for o in out["outputs"]}
    print("  labels:", res.get("label"))
    print("  probabilities:", [round(p, 3) for p in res.get("probabilities", [])])
    print("  (ожидаем label 0 и 2)")


def resnet_test():
    print("== resnet50 ==")
    import random
    random.seed(0)
    n = 3 * 224 * 224
    vec = [random.random() for _ in range(n)]  # случайная «картинка»
    payload = {"inputs": [
        {"name": "data", "shape": [1, 3, 224, 224], "datatype": "FP32", "data": vec},
    ]}
    out = post("/v2/models/resnet50/infer", payload)
    logits = out["outputs"][0]["data"]
    top = max(range(len(logits)), key=lambda i: logits[i])
    print(f"  вернул {len(logits)} логитов, top-класс ImageNet = {top}")
    print("  (класс случайный — вход шум; главное, GPU-инференс ответил)")


if __name__ == "__main__":
    print("server ready:", get("/v2/health/ready") if False else "проверь /v2/health/ready")
    iris_test()
    resnet_test()
    print("\nOK — оба инференса отработали.")
