FROM python:3.11-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    adb \
    android-sdk-platform-tools \
    ca-certificates \
    tini \
  && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN adduser --disabled-password --gecos "" agentbeats
WORKDIR /home/agentbeats/mas

COPY agentbeats/requirements.txt /tmp/agentbeats-requirements.txt
RUN python -m pip install --no-cache-dir \
  -r /tmp/agentbeats-requirements.txt \
  "droidrun[anthropic,deepseek,google,ollama,openai,openrouter]>=0.3.2"

COPY --chown=agentbeats:agentbeats agentbeats /home/agentbeats/mas/agentbeats

USER agentbeats
EXPOSE 9010

ENTRYPOINT ["/usr/bin/tini", "--", "python", "agentbeats/purple/droidrun_baseline/src/server.py"]
CMD ["--host", "0.0.0.0", "--port", "9010"]
