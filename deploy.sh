#!/bin/bash

echo "Stopping existing containers..."
docker compose down

echo "Building and starting services..."
docker compose up -d --build

echo "Waiting for services to be ready..."
sleep 10

echo "Services are running!"
echo "You can check the logs with: docker-compose logs -f"
echo "Access your app at: http://localhost (or your configured domain)"
