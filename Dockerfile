# ==========================
# Builder stage
# ==========================
FROM python:3.13-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml uv.lock ./

# Код нужен для сборки (uv sync требует проект в рабочей директории).
COPY src ./src
COPY fake_provider ./fake_provider
COPY main.py config.py alembic.ini gunicorn.conf.py ./

RUN uv sync --no-dev --frozen

# ==========================
# Runtime stage
# ==========================
FROM python:3.13-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv

COPY . .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["gunicorn", "main:app", "-c", "gunicorn.conf.py"]
