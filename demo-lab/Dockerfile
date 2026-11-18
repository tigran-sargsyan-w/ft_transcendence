FROM python:3.12-alpine

WORKDIR /app
RUN addgroup -S demo && adduser -S -G demo demo
COPY --chown=demo:demo server.py /app/server.py
USER demo
EXPOSE 8080
CMD ["python", "-u", "/app/server.py"]
