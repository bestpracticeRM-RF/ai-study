import mlflow
import os
from sklearn.linear_model import LogisticRegression
from sklearn.datasets import load_iris

# Настраиваем доступы к локальной инфраструктуре
os.environ["MLFLOW_S3_ENDPOINT_URL"] = "http://minio.mlops.local"
os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin123"

mlflow.set_tracking_uri("http://mlflow.mlops.local")
mlflow.set_experiment("Phase1_Test")

# Учим простую модель
X, y = load_iris(return_X_y=True)
model = LogisticRegression(max_iter=200)

with mlflow.start_run():
    mlflow.log_param("max_iter", 200)
    model.fit(X, y)
    score = model.score(X, y)
    mlflow.log_metric("accuracy", score)
    
    # Загружаем модель в S3 (MinIO)
    mlflow.sklearn.log_model(model, "iris_model")

print(f"Эксперимент завершен! Accuracy: {score}")
