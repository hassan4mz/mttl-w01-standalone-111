FROM python:3.12-alpine
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY backend.py ha_bridge.py backup_manager.py firmware_manager.py firmware_patch.py /app/
COPY static /app/static
VOLUME ["/data"]
EXPOSE 10086 8080 10080
CMD ["python","/app/backend.py","--data","/data"]
