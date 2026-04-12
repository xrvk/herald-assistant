FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home appuser

COPY main.py .
COPY demo/ demo/

RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser
CMD ["python", "-u", "main.py"]
