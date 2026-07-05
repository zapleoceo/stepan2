FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY migrations ./migrations
COPY docs ./docs
COPY alembic.ini ./

EXPOSE 8000
# --timeout-graceful-shutdown: on SIGTERM (deploy recreate) stop accepting new
# connections but let in-flight requests finish before exiting — cuts the mid-response
# resets (nginx "recv() failed") that surface as 502s during a deploy. Must be <=
# the compose stop_grace_period so docker doesn't SIGKILL mid-drain.
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--timeout-graceful-shutdown", "20"]
