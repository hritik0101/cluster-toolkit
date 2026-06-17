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

import argparse
import json
import os
import sys
import subprocess
from google.cloud import storage

BUCKET_NAME = "g-ift-agent-bucket"

def main():
    parser = argparse.ArgumentParser(description="Trigger Failure Triage Agent")
    parser.add_argument("--build-id", required=True, help="Build ID")
    parser.add_argument("--project-id", required=True, help="Project ID")
    args = parser.parse_args()

    build_id = args.build_id
    project_id = args.project_id

    print(f"Triggering pipeline for build_id: {build_id}, project_id: {project_id}")

    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)

    if not bucket.exists():
        print(f"Error: Bucket {BUCKET_NAME} does not exist.")
        sys.exit(1)

    # 1. Copy state.json from root to the build_id folder
    source_state_blob = bucket.blob("state.json")
    if not source_state_blob.exists():
        print(f"Error: Source state.json (gs://{BUCKET_NAME}/state.json) does not exist.")
        sys.exit(1)

    destination_state_path = f"{build_id}/state.json"
    bucket.copy_blob(source_state_blob, bucket, destination_state_path)
    print(f"Copied gs://{BUCKET_NAME}/state.json to gs://{BUCKET_NAME}/{destination_state_path}")

    # 2. Call the orchestrator
    print(f"Starting orchestrator for build_id: {build_id}...")
    try:
        subprocess.run([sys.executable, "orchestrator.py", "--build-id", build_id, "--project-id", project_id], check=True)
        print("Orchestrator completed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"Orchestrator failed with exit code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
