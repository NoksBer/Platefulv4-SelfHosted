FROM python:3.12-slim
WORKDIR /app
COPY server.py seed.py seed_extra.py ./
COPY static ./static
ENV PLATEFUL_DB=/data/plateful.db PLATEFUL_PORT=8080
VOLUME /data
EXPOSE 8080
CMD ["python", "server.py"]
