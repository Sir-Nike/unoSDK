FROM node:24-slim
WORKDIR /app
LABEL org.triad-uno.protocol="1.0" org.triad-uno.runtime="2" org.triad-uno.sdk="uno-sdk-2" \
      org.triad-uno.cpu="1" org.triad-uno.ram-bytes="1073741824" \
      org.triad-uno.scratch-bytes="1073741824" org.triad-uno.pids="128" \
      org.triad-uno.gpu="false" org.triad-uno.gpu-bytes="0"

COPY src/ ./src/
COPY submission.json package.json ./

USER 10000:10000

ENTRYPOINT ["node", "src/agent.ts"]
