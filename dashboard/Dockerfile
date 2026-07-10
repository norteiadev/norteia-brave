# Dashboard image (dev) — Next.js 16 under Bun on Node 22 (the repo target).
# docker-compose bind-mounts ./dashboard over /app/dashboard and keeps node_modules in a
# named volume (seeded from this build) so the container's linux modules don't clash with
# the host's. `next dev` binds 0.0.0.0 so the mapped port is reachable from the host.
FROM node:22-bookworm-slim

# Bun (pinned to the repo's version) as the package manager + runner.
RUN npm install -g bun@1.3.13

WORKDIR /app/dashboard

# Deps first (cached layer).
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

# Source (shadowed at runtime by the bind-mount; kept for a runnable standalone image).
COPY . ./

EXPOSE 3000

CMD ["bun", "x", "next", "dev", "-H", "0.0.0.0", "-p", "3000"]
