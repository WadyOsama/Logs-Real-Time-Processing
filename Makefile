start-pipeline:
	@echo "Starting the pipeline..."
	docker compose up -d
	docker compose --profile manual create
	@echo "Pipeline started successfully."
stop-pipeline:
	@echo "Stopping the pipeline..."
	docker compose --profile manual down
	docker compose down --remove-orphans
	@echo "Pipeline stopped successfully."
restart-pipeline:
	@echo "Restarting the pipeline..."
	docker compose down
	docker compose up -d
	@echo "Pipeline restarted successfully."
status-pipeline:
	@echo "Checking the status of the pipeline..."
	docker compose ps
	@echo "Pipeline status checked successfully."