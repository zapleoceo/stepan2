FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY migrations ./migrations
COPY docs ./docs
COPY alembic.ini ./

# Run as a non-root user: with docker.sock exposed on this host for the deploy path, an RCE in
# a dependency parsing untrusted IG payloads must not be root-in-container. App state lives in
# Postgres, not the filesystem — единственное исключение sim_runs, отчёты круга проверки
# продаж. Каталог создаётся ЗДЕСЬ, а не в рантайме: docker наследует владельца из образа
# только когда каталог в образе есть. Без этой строки том stepan2_simruns рождается от root,
# и приложение под app получает PermissionError ровно в конце раунда, когда отчёт уже готов.
RUN adduser --disabled-password --gecos "" --uid 10001 app \
    && mkdir -p /app/sim_runs && chown -R app /app
USER app

EXPOSE 8000
# --timeout-graceful-shutdown: on SIGTERM (deploy recreate) stop accepting new
# connections but let in-flight requests finish before exiting — cuts the mid-response
# resets (nginx "recv() failed") that surface as 502s during a deploy. Must be <=
# the compose stop_grace_period so docker doesn't SIGKILL mid-drain.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--timeout-graceful-shutdown", "20"]
