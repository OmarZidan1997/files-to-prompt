FROM python:3.12-slim

WORKDIR /app

# Install the package with its web extras.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[web]"

# Do NOT bake secrets into the image. Provide them at run time, e.g.:
#   docker run --env-file .env -p 8000:8000 turnover-web
# or with -e HOSPITABLE_ACCESS_TOKEN=... -e ANTHROPIC_API_KEY=... etc.
EXPOSE 8000
ENV PORT=8000
CMD ["hospitality-web"]
