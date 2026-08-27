FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .
RUN useradd --system --uid 10001 syncbridge && mkdir -p /data && chown syncbridge /data
USER syncbridge
ENV SYNCBRIDGE_DB=/data/syncbridge.db
EXPOSE 8080
CMD ["syncbridge", "serve"]
