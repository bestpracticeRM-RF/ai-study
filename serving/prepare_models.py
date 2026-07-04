#!/usr/bin/env python3
"""
Подготовка model repository для Triton и заливка в MinIO (bucket triton-models).

Шаги:
  1. Iris  -> ONNX  (обучение + skl2onnx)         -> model_repository/iris/1/model.onnx
  2. ResNet-50 ONNX (скачать из ONNX model zoo)   -> model_repository/resnet50/1/model.onnx
  3. upload всей структуры (+ config.pbtxt) в MinIO S3

Зависимости (в отдельном venv):
  pip install scikit-learn skl2onnx onnx onnxruntime boto3

Запуск:  python3 serving/prepare_models.py
config.pbtxt лежат рядом в model_repository/<model>/ и коммитятся в git.
Сами .onnx НЕ коммитятся (большие) — регенерируются этим скриптом.
"""
import os, glob, urllib.request
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "model_repository")

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio.mlops.local")
MINIO_KEY = os.environ.get("MINIO_KEY", "")
MINIO_SECRET = os.environ.get("MINIO_SECRET", "")
if not (MINIO_KEY and MINIO_SECRET):
    raise SystemExit("Задай креды через env: MINIO_KEY=... MINIO_SECRET=... python3 serving/prepare_models.py")
BUCKET = "triton-models"

RESNET_URL = ("https://github.com/onnx/models/raw/main/validated/vision/"
              "classification/resnet/model/resnet50-v2-7.onnx")


def make_iris():
    from sklearn.datasets import load_iris
    from sklearn.linear_model import LogisticRegression
    from skl2onnx import to_onnx
    X, y = load_iris(return_X_y=True)
    X = X.astype(np.float32)
    model = LogisticRegression(max_iter=300).fit(X, y)
    onx = to_onnx(model, X[:1], target_opset=17,
                  options={id(model): {"zipmap": False}})
    out = os.path.join(REPO, "iris/1/model.onnx")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(onx.SerializeToString())
    print("iris ONNX ->", out)


def fetch_resnet():
    out = os.path.join(REPO, "resnet50/1/model.onnx")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.exists(out) and os.path.getsize(out) > 1_000_000:
        print("resnet50 ONNX уже есть, пропуск")
        return
    print("качаю resnet50 ONNX...")
    urllib.request.urlretrieve(RESNET_URL, out)
    print("resnet50 ONNX ->", out, os.path.getsize(out), "bytes")


def upload():
    import boto3
    from botocore.client import Config
    s3 = boto3.client("s3", endpoint_url=MINIO_ENDPOINT,
                      aws_access_key_id=MINIO_KEY, aws_secret_access_key=MINIO_SECRET,
                      config=Config(signature_version="s3v4"), region_name="us-east-1")
    try:
        s3.create_bucket(Bucket=BUCKET)
    except Exception:
        pass
    for f in glob.glob(REPO + "/**", recursive=True):
        if os.path.isfile(f):
            key = os.path.relpath(f, REPO)
            s3.upload_file(f, BUCKET, key)
            print("uploaded:", key)


if __name__ == "__main__":
    make_iris()
    fetch_resnet()
    upload()
    print("\nГотово. Triton подхватит из s3://.../triton-models (reload или рестарт пода).")
