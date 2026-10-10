FROM python:3.13-slim
RUN pip install --no-cache-dir boto3==1.43.110
COPY backend/cloudhub/container_app.py /app/app.py
USER 65534:65534
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "/app/app.py"]
