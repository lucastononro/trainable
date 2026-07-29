# Trainable RunPod worker image.
#
# One image, three roles, selected by TRAINABLE_ROLE (or an explicit
# docker start cmd):
#   runner   serverless code-runner handler (execute-code sandboxes)
#   kernel   notebook kernel HTTP gateway (long-lived pod)
#   serving  model-serving launcher (serverless endpoint per model)
#
# The pip stack mirrors backend/services/sandbox.py:get_image() so agent
# code behaves identically on Modal and RunPod. Build + push with
# `make runpod-image` from the repo root (linux/amd64 — RunPod hosts are
# x86).

FROM python:3.11-slim

RUN pip install --no-cache-dir \
    pandas \
    numpy \
    matplotlib \
    seaborn \
    scikit-learn \
    xgboost \
    lightgbm \
    pyarrow \
    openpyxl \
    duckdb \
    imbalanced-learn \
    optuna \
    category_encoders \
    pandera \
    shap \
    statsmodels \
    ipykernel \
    jupyter_client \
    pypdf \
 && pip install --no-cache-dir \
    torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir tensorflow-cpu \
 && pip install --no-cache-dir \
    runpod \
    boto3 \
    fastapi \
    uvicorn \
    catboost \
    joblib

# Skills ship at /skills for parity with Modal's add_local_dir — scripts
# referenced from a SKILL.md stay reachable via execute-code.
COPY backend/skills /skills

COPY docker/runpod-worker/worker /opt/trainable/worker
COPY docker/runpod-worker/entrypoint.sh /opt/trainable/entrypoint.sh
RUN chmod +x /opt/trainable/entrypoint.sh

ENV PYTHONPATH=/opt/trainable
WORKDIR /opt/trainable

ENTRYPOINT ["/opt/trainable/entrypoint.sh"]
