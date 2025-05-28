# Chosen base image for Spark
# Use the official Bitnami Spark image as the base
FROM bitnami/spark:3.5.0

# Switch to root user to install dependencies
USER root

# Mount to the working directory
WORKDIR /opt/spark-apps

# Copt the requirements file
COPY spark-apps/requirements.txt /opt/spark-apps/requirements.txt

# Install necessary packages
RUN pip install --no-cache-dir -r requirements.txt

# Set the user to run the application
USER 1001

# Set the working directory for Spark applications
WORKDIR /opt/spark-apps
