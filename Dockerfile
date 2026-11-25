FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home appuser
COPY --chown=appuser:appuser backend ./backend
COPY --chown=appuser:appuser scripts ./scripts
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser automation/n8n_workflow.json ./automation/n8n_workflow.json
COPY --chown=appuser:appuser pytest.ini ./pytest.ini
COPY --chown=appuser:appuser data/processed/stocks_processed.csv ./data/processed/stocks_processed.csv
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
