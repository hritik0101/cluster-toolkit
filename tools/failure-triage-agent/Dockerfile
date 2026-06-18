# Copyright 2026 "Google LLC"
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Use the official lightweight Python image.
FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Knative logs
ENV PYTHONUNBUFFERED True
ENV CLOUD_RUN true

# Install curl, apt-transport-https, gnupg, and the Google Cloud SDK (gcloud)
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y curl apt-transport-https ca-certificates gnupg \
    && curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
    && apt-get update && apt-get install -y google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Copy local code to the container image.
ENV APP_HOME /app
WORKDIR $APP_HOME
COPY . ./

# Install production dependencies.
RUN pip install --no-cache-dir -r requirements.txt

# Switch to the app directory so that main.py and orchestrator.py work properly
WORKDIR /app/app

# Run the web service on container startup using gunicorn webserver.
# - workers 1 and threads 1 allows handling multiple concurrent requests per container instance.
# - Timeout is set to 0 to disable timeouts of the workers to allow Cloud Run to handle scaling.
CMD exec gunicorn --bind :$PORT --workers 1 --threads 1 --timeout 0 main:app
