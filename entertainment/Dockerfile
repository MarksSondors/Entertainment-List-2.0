FROM python:alpine

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apk update && apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    postgresql-dev \
    postgresql-client \
    netcat-openbsd \
    cmake \
    make

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies.
# The BuildKit cache mount persists pip's wheel cache across builds, so
# heavy source builds (notably `implicit`) compile once and are reused on
# every later build — cuts deploy time from minutes to seconds when
# requirements are unchanged.
RUN --mount=type=cache,target=/root/.cache/pip,id=pip-cache,sharing=locked \
    pip install -r requirements.txt

# Copy the project files
COPY . .

# Ensure entrypoint.sh has correct line endings and is executable
RUN sed -i 's/\r$//' /app/entrypoint.prod.sh && \
    chmod +x /app/entrypoint.prod.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.prod.sh"]