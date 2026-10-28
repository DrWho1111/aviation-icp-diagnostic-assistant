# Local iteration only. First build deploy/Dockerfile as icp-final-validation:20261024.
FROM icp-final-validation:20261024
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY data/processed/ /app/data/processed/
COPY frontend/dist/ /app/frontend/dist/
