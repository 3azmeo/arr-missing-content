# Use lightweight Python Alpine image
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the script
COPY main.py .

# Ensure the data directory exists
RUN mkdir -p /data

# Run the script
CMD ["python", "-u", "main.py"]