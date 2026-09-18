FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=10s --retries=3 CMD wget -qO- http://127.0.0.1/healthz || exit 1
