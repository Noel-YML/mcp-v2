FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py config.py ./
COPY fabric_client/ ./fabric_client/
COPY products/ ./products/
COPY tools/ ./tools/
COPY skills/ ./skills/

ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
