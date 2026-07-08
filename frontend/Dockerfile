ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

FROM node:20-slim AS deps

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

WORKDIR /app/frontend

RUN corepack enable && corepack prepare pnpm@8.15.9 --activate

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.pnpm-store \
    pnpm install --frozen-lockfile --store-dir=/root/.pnpm-store

FROM node:20-slim AS builder

ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG http_proxy
ARG https_proxy

WORKDIR /app/frontend

RUN corepack enable && corepack prepare pnpm@8.15.9 --activate

ARG NEXT_PUBLIC_API_BASE=https://agent.yunmiao.site
ARG NEXT_PUBLIC_LANGFUSE_BASE_URL=https://cloud.langfuse.com
ARG NEXT_PUBLIC_LANGFUSE_PROJECT_ID=
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_LANGFUSE_BASE_URL=$NEXT_PUBLIC_LANGFUSE_BASE_URL \
    NEXT_PUBLIC_LANGFUSE_PROJECT_ID=$NEXT_PUBLIC_LANGFUSE_PROJECT_ID

COPY --from=deps /app/frontend/node_modules ./node_modules
COPY frontend ./
RUN --mount=type=cache,target=/app/frontend/.next/cache \
    pnpm build

FROM node:20-slim AS runner

WORKDIR /app/frontend

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN corepack enable && corepack prepare pnpm@8.15.9 --activate

COPY --from=builder /app/frontend/package.json ./package.json
COPY --from=builder /app/frontend/pnpm-lock.yaml ./pnpm-lock.yaml
COPY --from=builder /app/frontend/node_modules ./node_modules
COPY --from=builder /app/frontend/.next ./.next
COPY --from=builder /app/frontend/public ./public
COPY --from=builder /app/frontend/next.config.mjs ./next.config.mjs

EXPOSE 3000

CMD ["pnpm", "start"]
