FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir .

RUN mkdir -p /app/.automate-jev
VOLUME ["/app/.automate-jev"]
EXPOSE 8000

CMD ["uvicorn", "automate_jev.api:app", "--host", "0.0.0.0", "--port", "8000"]
