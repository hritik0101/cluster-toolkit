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

# We use the Google Cloud SDK base image because the script runs 'gcloud' commands
FROM docker.io/google/cloud-sdk:slim

# Set the working directory inside our container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies (the base image already has Python and pip)
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy the rest of your agent's code into the container
COPY . .

# Tell Docker what command to run when the container starts
ENTRYPOINT ["python3", "orchestrator.py"]
