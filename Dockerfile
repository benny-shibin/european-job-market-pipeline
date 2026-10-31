FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --disable-pip-version-check -r requirements.txt
COPY src ./src
COPY dashboard ./dashboard
COPY config ./config
COPY sql ./sql
COPY .streamlit ./.streamlit
RUN mkdir -p /app/data
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "dashboard/app.py", "--server.address=0.0.0.0", "--server.headless=true"]

FROM runtime AS test
COPY requirements-dev.txt ./
RUN python -m pip install --disable-pip-version-check -r requirements-dev.txt
COPY tests ./tests
COPY scripts ./scripts
CMD ["python", "-m", "pytest", "-q"]
