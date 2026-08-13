# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=22
FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build

ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set registry "${NPM_REGISTRY}" \
    && npm ci --no-audit --no-fund

COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine

COPY deploy/frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /build/frontend/dist /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
