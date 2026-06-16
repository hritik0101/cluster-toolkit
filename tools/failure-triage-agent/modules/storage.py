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

import os
import logging
from google.cloud import storage

logger = logging.getLogger("storage")
BUCKET_NAME = "hpc-toolkit-failure-triage-bucket"

def sync_state(build_id):
    """Upload local state file to GCS."""
    local_path = os.path.join(build_id, "state", f"run_{build_id}.json")
    gcs_path = f"{build_id}/state.json"
    _upload(local_path, gcs_path)

def upload_report(build_id):
    """Upload final report to GCS."""
    local_path = os.path.join(build_id, f"report_{build_id}.md")
    gcs_path = f"{build_id}/report.md"
    _upload(local_path, gcs_path)

def _upload(local_path, gcs_path, retries=3):
    import time
    for attempt in range(retries):
        try:
            if not os.path.exists(local_path):
                logger.warning(f"Local file {local_path} does not exist. Skipping upload.")
                return
            client = storage.Client()
            bucket = client.bucket(BUCKET_NAME)
            
            # Create bucket if it doesn't exist
            if not bucket.exists():
                logger.info(f"Bucket {BUCKET_NAME} does not exist. Creating it.")
                bucket.create()
                
            blob = bucket.blob(gcs_path)
            blob.upload_from_filename(local_path)
            logger.info(f"Synced {local_path} → gs://{BUCKET_NAME}/{gcs_path}")
            return
        except Exception as e:
            logger.warning(f"GCS upload attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    logger.error(f"Failed to upload {local_path} to GCS after {retries} retries")
