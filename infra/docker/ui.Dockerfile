# Two targets from one file:
#   dev     (infra/compose.yaml): the Vite dev server with live reload, source bind-mounted.
#   release (infra/compose.release.yaml, test and prod): the built UI served by Caddy, which
#           also passes /api to the app, so the browser talks to one address (PRD H2).
FROM node:22-alpine AS deps
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM deps AS build
COPY frontend/ ./
RUN npm run build

FROM caddy:2.10-alpine AS release
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /ui/dist /srv
EXPOSE 8080

FROM deps AS dev
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
