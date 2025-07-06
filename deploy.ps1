Write-Host "Stopping existing containers..." -ForegroundColor Yellow
docker compose down

Write-Host "Building and starting services..." -ForegroundColor Yellow
docker compose up -d --build

Write-Host "Waiting for services to be ready..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

Write-Host "Services are running!" -ForegroundColor Green
Write-Host "You can check the logs with: docker-compose logs -f" -ForegroundColor Cyan
Write-Host "Access your app at: http://localhost (or your configured domain)" -ForegroundColor Cyan
