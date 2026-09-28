# chrome.net.ua AI Assistant — agent backend (stdlib + SymPy)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# deps first so layer caching survives source edits
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/

# run unprivileged
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Cloud Run injects PORT and requires binding all interfaces
ENV CHATBOT_HOST=0.0.0.0
EXPOSE 8080
CMD ["sh", "-c", "CHATBOT_PORT=${PORT:-8080} exec python server/chatbot.py"]
