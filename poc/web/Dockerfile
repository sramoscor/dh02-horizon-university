# C-WEB: Streamlit como cliente HTTP de C-API (E2-ADR-009). No contiene el backend.
FROM python:3.13.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app
WORKDIR /app
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt
COPY . ./web/
RUN useradd --create-home --uid 1000 app && chown -R app /app
USER app
EXPOSE 8501
CMD ["streamlit", "run", "web/app.py", "--server.port=8501", "--server.address=0.0.0.0", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
