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

import logging
import json
import re
import sys
sys.path.append("/usr/lib/python3/dist-packages")
import urllib.request
import yaml
from google.cloud import storage
import os
from modules.preprocessor import process_in_memory
from modules.storage import sync_state

logger = logging.getLogger("intake")

def download_logs(build_id: str, project_id: str) -> str:
    """Fetches build logs from GCS."""
    bucket_name = f"{project_id}.cloudbuild-logs.googleusercontent.com"
    blob_name = f"log-{build_id}.txt"
    
    logger.info(f"Attempting to download gs://{bucket_name}/{blob_name}")
    logger.debug(f"GCS client initialization starting. Bucket: '{bucket_name}', Blob: '{blob_name}'")
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    
    try:
        logger.debug("Fetching blob content...")
        content = blob.download_as_text()
        logger.info("Log file downloaded successfully")
        logger.debug(f"Successfully downloaded {len(content)} characters of build log content")
        return content
    except Exception as e:
        logger.error(f"Failed to download log: {e}")
        raise

def scrape_identifiers(log_content: str) -> dict:
    """Scrapes the log content for project, zone, region, blueprint, and commit."""
    logger.info("Starting scraping of log file")
    logger.debug(f"Log content total length is {len(log_content)} characters")
    
    data = {
        "project": "",
        "zone": "",
        "region": "",
        "blueprint": "",
        "commit": "",
        "test_file": "",
        "platform": "",
        "vars_file_path": "",
        "repo": ""
    }
    
    # Regex searches
    logger.debug("Performing regex matching for build metadata...")
    project_match = re.search(r"project[:=]\s*([a-zA-Z0-9-]+)", log_content, re.IGNORECASE)
    zone_match = re.search(r"zone[:=]\s*([a-zA-Z0-9-]+)", log_content, re.IGNORECASE)
    region_match = re.search(r"region[:=]\s*([a-zA-Z0-9-]+)", log_content, re.IGNORECASE)
    commit_match = re.search(r"branch\s+([a-f0-9]{40})\s+->\s+FETCH_HEAD", log_content)
    test_file_match = re.search(r"check_for_running_build\":\s*(tools/[^\s]+)", log_content)
    repo_match = re.search(r"From\s+https://github\.com/([^\s]+)", log_content)
    
    # Check for GKE_VARS_FILE or SLURM_VARS_FILE line first
    vars_file_match = re.search(r"([a-zA-Z0-9_-]+)_VARS_FILE[:=]\s*[\"']?([^\s\"']+)[\"']?", log_content)
    if vars_file_match:
        data["vars_file_path"] = vars_file_match.group(2)
        logger.info(f"Scraped vars file path from VARS_FILE line: {data['vars_file_path']}")
    else:
        logger.debug("VARS_FILE line match not found in logs")
        
    if project_match:
        data["project"] = project_match.group(1)
        logger.info(f"Scraped project ID: {data['project']}")
    else:
        logger.debug("Project ID regex match not found in logs")
        
    if zone_match:
        data["zone"] = zone_match.group(1)
        logger.info(f"Scraped zone: {data['zone']}")
    else:
        logger.debug("Zone regex match not found in logs")
        
    if region_match:
        data["region"] = region_match.group(1)
        logger.info(f"Scraped region: {data['region']}")
    else:
        logger.debug("Region regex match not found in logs")
        
    if commit_match:
        data["commit"] = commit_match.group(1)
        logger.info(f"Scraped commit hash: {data['commit']}")
    else:
        logger.debug("Commit hash regex match not found in logs")
        
    if test_file_match:
        data["test_file"] = test_file_match.group(1)
        logger.info(f"Scraped test file path: {data['test_file']}")
    else:
        logger.debug("Test file path regex match not found in logs")

    if repo_match:
        data["repo"] = repo_match.group(1)
        logger.info(f"Scraped repository: {data['repo']}")
    else:
        logger.debug("Repository regex match not found in logs")
        
    return data

def download_github_file(commit: str, file_path: str, repo: str = "GoogleCloudPlatform/cluster-toolkit") -> str:
    """Downloads a file from GitHub at a specific commit."""
    if not commit or not file_path:
        logger.warning("Commit or file path missing, cannot download from GitHub")
        return ""
        
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"
    logger.info(f"Attempting to download {url}")
    
    try:
        logger.debug(f"Fetching URL: {url}")
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read().decode('utf-8')
            logger.info(f"Successfully downloaded {file_path} from GitHub")
            logger.debug(f"Downloaded file size: {len(content)} characters")
            return content
    except Exception as e:
        logger.error(f"Failed to download {file_path} from GitHub: {e}")
        return ""


def run_intake(build_id: str, project_id: str, commands: dict, save_raw: bool = True, save_preprocessed: bool = False, default_commit: str = "b64c135c8c0a9752a0ee5d086966cbd12fcfe4ee", default_repo: str = "GoogleCloudPlatform/cluster-toolkit"):
    """Main function for Module 1."""
    # Configure file logger for the intake module
    for h in logger.handlers[:]:
        logger.removeHandler(h)
        
    IS_CLOUD_RUN = os.environ.get('CLOUD_RUN') == 'true'
    if IS_CLOUD_RUN:
        logger.propagate = True
        logger.setLevel(logging.DEBUG)
    else:
        log_file = os.path.join(build_id, "logs", f"intake_{build_id}.log")
        file_handler = logging.FileHandler(log_file, mode='w')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    logger.info(f"Starting Intake Phase for Build ID: {build_id}")
    logger.info(f"Verbose log file initialized at {log_file}")
    
    # 1. Download
    log_content = download_logs(build_id, project_id)
    
    # 2. Scrape
    identifiers = scrape_identifiers(log_content)
    
    if not identifiers.get("commit"):
        identifiers["commit"] = default_commit if default_commit else "b64c135c8c0a9752a0ee5d086966cbd12fcfe4ee"
        logger.info(f"Using default commit: {identifiers['commit']}")
        
    if not identifiers.get("repo"):
        identifiers["repo"] = default_repo if default_repo else "GoogleCloudPlatform/cluster-toolkit"
        logger.info(f"Using default repo: {identifiers['repo']}")

    logger.debug(f"Identifiers scraped from build log: {identifiers}")
    
    repo_name = identifiers.get("repo") or "GoogleCloudPlatform/cluster-toolkit"
    
    # 3. Download test file if possible
    test_file_content = ""
    if identifiers["commit"] and identifiers["test_file"]:
        logger.debug(f"Attempting to download test configuration file: '{identifiers['test_file']}' at commit '{identifiers['commit']}'")
        test_file_content = download_github_file(identifiers["commit"], identifiers["test_file"], repo=repo_name)
    else:
        logger.warning("Commit or test_file path not available, skipping test file download")
        
    # Get vars_file_path from identifiers or scrape it from test file
    vars_file_path = identifiers.pop("vars_file_path", "")
    if not vars_file_path and test_file_content:
        vars_file_match = re.search(r"(tools/cloud-build/daily-tests/tests/[a-zA-Z0-9_-]+\.yml)", test_file_content)
        if vars_file_match:
            vars_file_path = vars_file_match.group(1)
            logger.info(f"Scraped vars file path from test file content: {vars_file_path}")

    # Platform will be determined from the blueprint file content later
    # 4. Download and parse vars file
    vars_file_content = ""
    if vars_file_path and identifiers.get("commit"):
        logger.info(f"Attempting to download vars file from GitHub: {vars_file_path}")
        vars_file_content = download_github_file(identifiers["commit"], vars_file_path, repo=repo_name)

    vars_data = {}
    if vars_file_content:
        try:
            vars_data = yaml.safe_load(vars_file_content) or {}
            logger.info("Successfully parsed vars file content as YAML")
        except Exception as e:
            logger.error(f"Failed to parse vars file content as YAML: {e}")

    # Extract blueprint from vars file content if available
    blueprint_yaml = vars_data.get("blueprint_yaml", "")
    if blueprint_yaml:
        # Strip "{{ workspace }}/" prefix
        blueprint_path = re.sub(r"^\{\{\s*workspace\s*\}\}/", "", blueprint_yaml)
        identifiers["blueprint"] = blueprint_path
        logger.info(f"Extracted blueprint path from vars file: {identifiers['blueprint']}")
    elif test_file_content:
        logger.debug("Extracting blueprint path from test file content...")
        blueprint_match = re.search(r"(?:EXAMPLE_BP|BLUEPRINT)=[\"']?([^\s\"'#]+)[\"']?", test_file_content)
        if blueprint_match:
            blueprint_path = blueprint_match.group(1).replace("/workspace/", "")
            identifiers["blueprint"] = blueprint_path
            logger.info(f"Extracted blueprint path from test file: {identifiers['blueprint']}")
        else:
            logger.warning("Could not find EXAMPLE_BP or BLUEPRINT pattern in test file content")

    # Extract blueprint_name
    blueprint_name = ""
    
    downloads_dir = os.path.join(build_id, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)
    preprocessed_dir = os.path.join(build_id, "preprocessed")
    os.makedirs(preprocessed_dir, exist_ok=True)
    
    clean_downloads_content = []
    preprocessed_context = []
    
    if log_content:
        if save_raw:
            log_file_name = f"log-{build_id}.txt"
            with open(os.path.join(downloads_dir, log_file_name), "w") as f:
                f.write(log_content)
            logger.info(f"Saved build log to downloads/{log_file_name}")
        clean_intake = process_in_memory(log_content, 'intake_log')
        if save_preprocessed:
            with open(os.path.join(preprocessed_dir, "clean_intake.txt"), "w") as f:
                f.write(clean_intake)
        preprocessed_context.append({
            "type": "intake_log",
            "content": clean_intake
        })

    if test_file_content and identifiers.get("test_file"):
        test_file_name = os.path.basename(identifiers["test_file"])
        if save_raw:
            with open(os.path.join(downloads_dir, test_file_name), "w") as f:
                f.write(test_file_content)
            logger.info(f"Saved test file to downloads/{test_file_name}")
        clean_content = process_in_memory(test_file_content, 'download')
        preprocessed_context.append({
            "type": "download",
            "file_name": test_file_name,
            "content": clean_content
        })
        if save_preprocessed:
            clean_downloads_content.append(f"--- File: {test_file_name} ---")
            clean_downloads_content.append(clean_content)
            clean_downloads_content.append("")

    if vars_file_content and vars_file_path:
        vars_file_name = os.path.basename(vars_file_path)
        if save_raw:
            with open(os.path.join(downloads_dir, vars_file_name), "w") as f:
                f.write(vars_file_content)
            logger.info(f"Saved vars file to downloads/{vars_file_name}")
        clean_content = process_in_memory(vars_file_content, 'download')
        preprocessed_context.append({
            "type": "download",
            "file_name": vars_file_name,
            "content": clean_content
        })
        if save_preprocessed:
            clean_downloads_content.append(f"--- File: {vars_file_name} ---")
            clean_downloads_content.append(clean_content)
            clean_downloads_content.append("")

    if identifiers.get("blueprint"):
        blueprint_content = ""
        # Try downloading from GitHub first (using commit)
        if identifiers.get("commit"):
            logger.info("Attempting to download blueprint file from GitHub...")
            blueprint_content = download_github_file(identifiers["commit"], identifiers["blueprint"], repo=repo_name)
        

        if blueprint_content:
            blueprint_file_name = os.path.basename(identifiers["blueprint"])
            if save_raw:
                with open(os.path.join(downloads_dir, blueprint_file_name), "w") as f:
                    f.write(blueprint_content)
                logger.info(f"Saved blueprint to downloads/{blueprint_file_name}")
            clean_content = process_in_memory(blueprint_content, 'download')
            preprocessed_context.append({
                "type": "download",
                "file_name": blueprint_file_name,
                "content": clean_content
            })
            if save_preprocessed:
                clean_downloads_content.append(f"--- File: {blueprint_file_name} ---")
                clean_downloads_content.append(clean_content)
                clean_downloads_content.append("")

            # Determine platform from blueprint content
            blueprint_content_lower = blueprint_content.lower()
            if "controller" in blueprint_content_lower:
                identifiers["platform"] = "slurm"
                logger.info("Platform identified as 'slurm' from blueprint content")
            elif "gke" in blueprint_content_lower:
                identifiers["platform"] = "gke"
                logger.info("Platform identified as 'gke' from blueprint content")
            else:
                identifiers["platform"] = "unknown"
                logger.info("Platform identified as 'unknown' from blueprint content")

            try:
                bp_name_match = re.search(r"^blueprint_name:\s*([a-zA-Z0-9_-]+)", blueprint_content, re.MULTILINE)
                if bp_name_match:
                    blueprint_name = bp_name_match.group(1)
                    logger.info(f"Extracted blueprint name: {blueprint_name}")
            except Exception as e:
                logger.error(f"Failed to parse blueprint content: {e}")
            
    if clean_downloads_content and save_preprocessed:
        with open(os.path.join(preprocessed_dir, "clean_downloads.txt"), "w") as f:
            f.write('\n'.join(clean_downloads_content))

    identifiers["blueprint_name"] = blueprint_name

    if not identifiers.get("platform"):
        identifiers["platform"] = "unknown"

    # Determine deployment_name from the test variables data
    deployment_name = ""
    if "deployment_name" in vars_data:
        try:
            dep_template = str(vars_data["deployment_name"]).split("#")[0].strip().strip('"\'')
            # Replace {{ build }} with build_id[:6]
            deployment_name = re.sub(r"\{\{\s*build\s*\}\}", build_id[:6], dep_template)
            logger.info(f"Determined deployment name from vars data: {deployment_name}")
        except Exception as e:
            logger.error(f"Failed to parse deployment name from vars data: {e}")
            
    if not deployment_name:
        deployment_name = f"{build_id[:6]}-{blueprint_name}" if blueprint_name else f"{build_id[:6]}"
        logger.warning(f"Could not determine deployment name from vars file; falling back to: {deployment_name}")
        
    identifiers["deployment_name"] = deployment_name

    platform = identifiers.get("platform", "unknown")
    manifest = commands.get("to_be_executed", {}).get(platform, [])

    # 5. Update State JSON
    output_file = os.path.join(build_id, "state.json")
    try:
        with open(output_file, "r") as f:
            run_document = json.load(f)
    except FileNotFoundError:
        logger.warning(f"State file {output_file} not found. Creating a new one.")
        run_document = {}

    run_document.update({
        "build_id": build_id,
        "status": "in progress",
        "stage": "intake",
        "save_raw": save_raw,
        "save_preprocessed": save_preprocessed,
        "vars": vars_file_path,
        "preprocessed_context": preprocessed_context
    })
    run_document.update(identifiers)
    
    if "commands" not in run_document:
        run_document["commands"] = {"executed": {}, "to_be_executed": {}}
    run_document["commands"]["executed"] = {} if isinstance(manifest, dict) else []
    run_document["commands"]["to_be_executed"] = manifest
    
    logger.info(f"Updating state document at {output_file}")
    
    with open(output_file, "w") as f:
        json.dump(run_document, f, indent=2)
        
    sync_state(build_id)
        
    logger.info("Run document created successfully")
