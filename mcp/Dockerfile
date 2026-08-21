FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py config.py scope_token.py scope_context.py audit.py health.py ./
COPY fabric_client/ ./fabric_client/
COPY dmr/ ./dmr/
COPY tools/ ./tools/

ENV HOST=0.0.0.0
ENV PORT=8000
EXPOSE 8000

CMD ["python", "server.py"]
