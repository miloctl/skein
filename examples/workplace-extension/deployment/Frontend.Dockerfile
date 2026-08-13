ARG SKEIN_FRONTEND_HOST=skein-frontend-host:0.2.0
FROM ${SKEIN_FRONTEND_HOST} AS build

USER root
COPY dist/atlas-skein-extension-*.tgz /tmp/
RUN npm install --no-save --package-lock=false --legacy-peer-deps \
        /tmp/atlas-skein-extension-*.tgz \
    && rm /tmp/atlas-skein-extension-*.tgz

ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
# The core frontend/Dockerfile carries the same build argument. NEXT_PUBLIC_*
# values freeze at `next build`, so a derivative that drops this one ships a
# frontend that 401s against every SKEIN_API_TOKEN-protected backend.
ARG NEXT_PUBLIC_API_TOKEN=
ENV SKEIN_FRONTEND_EXTENSIONS=@atlas/skein-extension \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
COPY --from=build --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
