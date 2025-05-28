FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY log_generator.py log_generator.py

# Set the entrypoint to run the log generator
ENTRYPOINT ["python", "-u", "log_generator.py"]
