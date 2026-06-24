FROM arm32v7/python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static

VOLUME ["/data"]
EXPOSE 8080
CMD ["python", "/app/app.py"]
