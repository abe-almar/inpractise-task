# The job as an image, for schedulers that run containers (a Prefect Docker work pool, a machine without Python 3.12).
# `make run` on the host remains the one command; this runs the same job with the same exit code.
#   make docker-run                  build the image and run the job against the compose database
#   make docker-run CMD=check        any sec-filings subcommand
FROM python:3.12-slim

WORKDIR /app

# The package and its pinned dependencies first, so a change to the inputs below does not reinstall them.
COPY pyproject.toml constraints.txt ./
COPY src/ ./src/
RUN pip install --no-cache-dir -c constraints.txt . && rm -rf src

# The inputs, baked as defaults; docker-compose.yml mounts the working copies over them.
COPY companies.csv overrides.toml ./

# Nothing here needs root.
RUN useradd --uid 10001 --no-create-home job
USER job

# DATABASE_URL comes from the environment (docker-compose.yml, or -e). No .env is in the image (.dockerignore).
ENTRYPOINT ["sec-filings"]
CMD ["run"]
