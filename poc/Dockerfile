# Imagen única de C-API, C-WORKER, simuladores y pruebas (E2-ADR-002: mismo código para API y worker).
FROM python:3.13.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY . .
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "backend.bootstrap.api:app", "--host", "0.0.0.0", "--port", "8000"]
