FROM node:22-alpine

WORKDIR /opt/app

COPY apps/app6/package.json ./

RUN npm install --omit=dev

COPY apps/app6/server.js ./

EXPOSE 3000

CMD ["npm", "start"]
