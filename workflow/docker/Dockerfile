FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ARG AGENT_PKG=opencode-ai
RUN npm install -g ${AGENT_PKG} && npm cache clean --force

WORKDIR /workspace
