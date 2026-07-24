# syntax=docker/dockerfile:1.7
FROM node:24.4.0-alpine AS builder
ARG APP_VERSION=1.0.0
ARG RELEASE_COMMIT=development
ENV VITE_BUILD_VERSION=${APP_VERSION} VITE_RELEASE_COMMIT=${RELEASE_COMMIT}
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.0-alpine
ARG APP_VERSION=1.0.0
ARG RELEASE_COMMIT=development
LABEL org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.revision=${RELEASE_COMMIT}
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
EXPOSE 8080
