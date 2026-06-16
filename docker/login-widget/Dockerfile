FROM node:22-alpine AS build

WORKDIR /app

ARG VITE_AM_BASE_URL=https://am.jrsz.org:8443/am
ARG VITE_CLIENT_ID=sdkPublicClient
ARG VITE_JOURNEY=sdkUsernamePasswordJourney

ENV VITE_AM_BASE_URL=${VITE_AM_BASE_URL}
ENV VITE_CLIENT_ID=${VITE_CLIENT_ID}
ENV VITE_JOURNEY=${VITE_JOURNEY}

COPY apps/login-widget/package.json ./

RUN npm install

COPY apps/login-widget/ ./

RUN npm run build

FROM nginx:1.27-alpine

COPY docker/login-widget/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
