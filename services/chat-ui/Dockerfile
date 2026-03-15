FROM node:22-alpine

RUN npm install -g pnpm@10.5.1

WORKDIR /app

COPY package.json pnpm-lock.yaml ./

RUN pnpm install

COPY . .

EXPOSE 3000

CMD ["pnpm", "dev"]
