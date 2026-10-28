FROM node:22.20.0-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir --timeout 120 --retries 3 -r requirements-lock.txt
COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY data/processed/ ./data/processed/
COPY data/raw/documents/ ./data/raw/documents/
COPY outputs/evaluation.json ./outputs/evaluation.json
COPY --from=frontend /build/dist ./frontend/dist/
ENV PYTHONPATH=/app/backend PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python","-m","uvicorn","icp.app:app","--host","0.0.0.0","--port","8000"]

