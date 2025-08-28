FROM python:3.11-slim
WORKDIR /app


# Instalar dependencias del sistema si fueran necesarias (certificados, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
ca-certificates \
&& rm -rf /var/lib/apt/lists/*


COPY pyproject.toml README.md /app/
RUN pip install --no-cache-dir "mcp[cli]" python-gitlab


COPY server.py /app/server.py


# Variables opcionales: TRANSPORTE
# - STREAMABLE_HTTP=true -> expone HTTP (recomendado productivo)
# - SSE=true -> expone SSE
# - (default) stdio


EXPOSE 3002
CMD ["python", "/app/server.py"]