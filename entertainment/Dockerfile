FROM python:alpine

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apk update && apk add --no-cache \
    gcc \
    musl-dev \
    postgresql-dev \
    netcat-openbsd \
    bash

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project files
COPY . .

# Ensure entrypoint script has correct permissions
RUN chmod a+x /app/entrypoint.prod.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]