FROM node:22-alpine

WORKDIR /opt/app

COPY apps/app8/package.json ./

RUN npm install --omit=dev

COPY apps/app8/server.js ./

EXPOSE 3000

CMD ["npm", "start"]
