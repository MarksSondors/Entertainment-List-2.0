FROM python:alpine

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apk update && apk add --no-cache \
    gcc \
    musl-dev \
    postgresql-dev \
    postgresql-client \
    netcat-openbsd

# Copy the requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project files
COPY . .

# Ensure entrypoint.sh has correct line endings and is executable
RUN sed -i 's/\r$//' /app/entrypoint.prod.sh && \
    chmod +x /app/entrypoint.prod.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.prod.sh"]