FROM python:3.12-slim

WORKDIR /app
COPY app.py .
COPY static/ ./static/

EXPOSE 3000
CMD ["python3", "-u", "app.py"]
