FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./app.py
COPY verox_brain ./verox_brain
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
