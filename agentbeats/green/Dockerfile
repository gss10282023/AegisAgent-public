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
COPY requirements.txt /tmp/mas-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/agentbeats-requirements.txt -r /tmp/mas-requirements.txt

COPY --chown=agentbeats:agentbeats agentbeats /home/agentbeats/mas/agentbeats
COPY --chown=agentbeats:agentbeats mas-agents /home/agentbeats/mas/mas-agents
COPY --chown=agentbeats:agentbeats mas-conformance /home/agentbeats/mas/mas-conformance
COPY --chown=agentbeats:agentbeats mas-harness /home/agentbeats/mas/mas-harness
COPY --chown=agentbeats:agentbeats mas_harness /home/agentbeats/mas/mas_harness
COPY --chown=agentbeats:agentbeats mas-public /home/agentbeats/mas/mas-public
COPY --chown=agentbeats:agentbeats mas-spec /home/agentbeats/mas/mas-spec

USER agentbeats
EXPOSE 9009

ENTRYPOINT ["/usr/bin/tini", "--", "python", "agentbeats/green/src/server.py"]
CMD ["--host", "0.0.0.0", "--port", "9009"]
