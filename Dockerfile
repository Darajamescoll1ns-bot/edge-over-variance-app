# Decision-Quality Trainer — container image
# Builds a self-contained web service. The engine is stdlib; only the web layer
# needs the packages in requirements.txt.
FROM python:3.12-slim

WORKDIR /app

# Install web-layer dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + engine + templates/static.
COPY . .

# Seed the Markets Desk file so the /desk tab works on first boot.
RUN python3 -c "import news_desk; news_desk.save_desk(news_desk.build_default_desk(), news_desk.DEFAULT_DATE)"

# Persistent session data lives here; mount a volume in production.
ENV DQ_DATA_DIR=/app/data
EXPOSE 8000

# Honour the platform's $PORT if it injects one (Render/Railway/Fly/Heroku do).
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
