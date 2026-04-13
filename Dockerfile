FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
RUN chmod 644 main.py

RUN mkdir -p /app/data

CMD ["python", "-u", "main.py"]
