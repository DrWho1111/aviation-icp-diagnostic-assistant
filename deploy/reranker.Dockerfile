FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --timeout 120 --retries 3 torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --timeout 120 --retries 3 transformers==4.57.1 sentencepiece==0.2.1 safetensors==0.6.2
COPY scripts/rerank_server.py /app/rerank_server.py
ENV PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1
CMD ["python", "/app/rerank_server.py"]
