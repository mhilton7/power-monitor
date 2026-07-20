# syntax=docker/dockerfile:1.7
FROM node:24.4.0-alpine AS builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.0-alpine
COPY deploy/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /build/dist /usr/share/nginx/html
EXPOSE 8080
