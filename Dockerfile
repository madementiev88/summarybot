FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY rgo_bot/ rgo_bot/
COPY prompts/ prompts/
COPY alembic.ini .

# Logs directory
RUN mkdir -p logs

# Expose web port for Mini App
EXPOSE 8080

CMD ["python", "-m", "rgo_bot.bot.main"]
