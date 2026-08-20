# Ark onboarding bot — one image, two services.
#   Web bot:   python -m src.web         (default CMD; served behind the ingress)
#   Slack app: python -m src.slack_app   (override the command; Socket Mode, no ingress)
#
# Secrets/config are injected at runtime (never baked): CURSOR_API_KEY,
# ARK_ACCESS_TOKEN, SLACK_BOT_TOKEN, SLACK_APP_TOKEN, BASE_PATH=/onboarding-bot.
FROM python:3.12-slim

# System deps: curl for the Cursor agent CLI installer, ca-certificates for TLS.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install CPU-only torch first (far smaller than the default CUDA build), then
# the rest of the deps (sentence-transformers reuses the already-present torch).
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# Non-root runtime user.
RUN useradd --create-home --uid 10001 app
USER app
ENV HOME=/home/app \
    PATH=/home/app/.local/bin:/usr/local/bin:/usr/bin:/bin
WORKDIR /home/app/app

# Cursor agent CLI — the answer layer shells out to `agent` (found via PATH).
RUN curl https://cursor.com/install -fsS | bash

# App code (includes the committed data/ corpus).
COPY --chown=app:app . /home/app/app

# Pre-cache the embedding model so /ready and the first answer don't stall on a
# download at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Runtime defaults. CURSOR_WORKSPACE points at an empty dir so the agent does not
# scan the app tree on every answer (keeps latency down).
ENV WEB_HOST=0.0.0.0 \
    WEB_PORT=8765 \
    CURSOR_WORKSPACE=/home/app/agent-workspace
RUN mkdir -p /home/app/agent-workspace

EXPOSE 8765

# Default service is the web bot; the Slack deployment overrides the command with
# `python -m src.slack_app`.
CMD ["python", "-m", "src.web"]
