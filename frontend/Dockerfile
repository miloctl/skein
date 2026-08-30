FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

FROM dependencies AS build
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_API_TOKEN=
ARG NEXT_PUBLIC_SITE_URL=http://localhost:3000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_API_TOKEN=$NEXT_PUBLIC_API_TOKEN \
    NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 HOME=/tmp
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN chown -R node:0 /app && chmod -R g=u /app
USER node:0
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
    CMD wget -qO- http://localhost:3000/ > /dev/null || exit 1
CMD ["node", "server.js"]
