# Local, self-contained AggTool: API + web UI with filesystem storage (no AWS).
FROM python:3.13-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip wheel --no-cache-dir --wheel-dir /wheels ".[local]"

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGGTOOL_MODE=local \
    AGGTOOL_LOCAL_DATA_DIR=/data \
    AGGTOOL_WEB_DIR=/app/web
RUN useradd --create-home --uid 10001 aggtool \
    && mkdir -p /data && chown aggtool /data
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
WORKDIR /app
COPY web ./web
COPY samples ./samples
USER aggtool
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["aggtool", "serve", "--host", "0.0.0.0", "--port", "8000"]
