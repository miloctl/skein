FROM node:22-alpine AS build
WORKDIR /workplace

COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
COPY dist/skein-extension-api-1.0.0.tgz dist/skein-frontend-host-0.3.0.tgz ./dist/
RUN npm ci --no-audit --no-fund
COPY frontend ./frontend

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ARG NEXT_PUBLIC_API_TOKEN=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build:frontend

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 HOME=/tmp
COPY --from=build /workplace/dist/frontend ./
RUN chgrp -R 0 /app && chmod -R g=u /app
USER node:0
EXPOSE 3000
CMD ["node", "server.js"]
