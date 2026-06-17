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
import logging
import sys
from modules.intake import run_intake
from modules.collector import run_collector
from modules.analyzer import run_analyzer
import os
import json
import subprocess
import re
from modules.updater import run_updater
from modules.reporter import run_reporter
from modules.storage import sync_state, download_input, download_state

def update_state_file(build_id, status=None, stage=None):
    state_file = os.path.join(build_id, "state.json")
    if os.path.exists(state_file):
        try:
            with open(state_file, "r") as f:
                state_data = json.load(f)
            updated = False
            if status is not None:
                state_data["status"] = status
                updated = True
            if stage is not None:
                state_data["stage"] = stage
                updated = True
            if updated:
                with open(state_file, "w") as f:
                    json.dump(state_data, f, indent=2)
                sync_state(build_id)
        except Exception as e:
            logging.warning(f"Error updating state file: {e}")

def main():
    # Setup logging to show steps clearly
    
    parser = argparse.ArgumentParser(description="Failure Triage Agent")
    parser.add_argument("--build-id", required=True, help="Build ID for this run")
    args = parser.parse_args()

    build_id = args.build_id
    base_dir = build_id
    os.makedirs(os.path.join(base_dir, "logs"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "ssh_output"), exist_ok=True)
    os.makedirs(os.path.join(base_dir, "downloads"), exist_ok=True)

    download_input(build_id)
    download_state(build_id)

    input_file = os.path.join(build_id, "input.json")
    try:
        with open(input_file, "r") as f:
            input_data = json.load(f)
    except Exception as e:
        print(f"Error reading {input_file}: {e}")
        sys.exit(1)

    project_number = input_data.get("project_number")
    if not project_number:
        print("Error: project_number is missing from input.json")
        sys.exit(1)

    commands = input_data.get("commands")
    if not commands:
        print("Error: commands are missing from input.json")
        sys.exit(1)
        
    save_raw_val = input_data.get("save_raw", True)
    if isinstance(save_raw_val, str):
        save_raw = save_raw_val.lower() == "true"
    else:
        save_raw = bool(save_raw_val)
        
    save_preprocessed_val = input_data.get("save_preprocessed", False)
    if isinstance(save_preprocessed_val, str):
        save_preprocessed = save_preprocessed_val.lower() == "true"
    else:
        save_preprocessed = bool(save_preprocessed_val)
    
    # Reconfigure logging to write verbose output to a file instead of the terminal
    log_file = os.path.join(base_dir, "logs", f"orchestrator_{build_id}.log")
        
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[logging.FileHandler(log_file, mode='w')]
    )
    
    logging.info(f"Starting sequence for Build ID: {build_id}")
    logging.info(f"Verbose log file initialized at {log_file}")
    
    try:
        # Phase 1: Log Intake & Setup
        logging.info("Triggering Phase 1 (Intake)")
        
        default_commit = input_data.get("commit")
        default_repo = input_data.get("repo")
        if not default_repo and input_data.get("github_commit_link"):
            repo_match = re.search(r"github\.com/([^/]+/[^/]+)", input_data.get("github_commit_link"))
            if repo_match:
                default_repo = repo_match.group(1)
                
        run_intake(build_id, project_number, commands, save_raw, save_preprocessed, default_commit=default_commit, default_repo=default_repo)
        logging.info("Phase 1 completed successfully")
        
        state_file = os.path.join(build_id, "state.json")
        project = None
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as f:
                    state_data = json.load(f)
                project = state_data.get("project")
            except Exception as e:
                logging.warning(f"Error reading state file for project: {e}")
                
        if not project:
            logging.error("project could not be determined from intake")
            update_state_file(build_id, status="failed")
            sys.exit(1)
            
        logging.info(f"Using project: {project}")

        # Phase 2 & 3: Collector & LLM Analysis
        logging.info("Triggering Phase 2 & 3 (Collector & LLM Analysis)")
        analyzer_loops = int(input_data.get("analyzer_loops", 1))
        logging.info(f"Analyzer configured to run for {analyzer_loops} rounds.")
        for round_num in range(1, analyzer_loops + 1):

            update_state_file(build_id, stage="collector")
            run_collector(build_id)
            update_state_file(build_id, stage="analyzer")
            llm_json = run_analyzer(build_id, round_num, project=project)
            if llm_json and "error" not in llm_json:
                update_state_file(build_id, stage="updater")
                run_updater(build_id, llm_json, project=project)

            state_file = os.path.join(build_id, "state.json")
            if os.path.exists(state_file):
                try:
                    with open(state_file, "r") as f:
                        state_data = json.load(f)
                    commands_dict = state_data.get("commands", {})
                    if isinstance(commands_dict, dict):
                        to_execute = commands_dict.get("to_be_executed", [])
                        if not to_execute:
                            logging.info(f"No more commands to execute in round {round_num}. Breaking loop.")
                            break
                except Exception as e:
                    logging.warning(f"Error checking state file for commands: {e}")
        logging.info("Phase 2 & 3 (Collector & LLM Analysis) completed successfully")
        
        # Phase 4: Reporter
        logging.info("Triggering Phase 4 (Reporter)")
        update_state_file(build_id, stage="reporter")
        run_reporter(build_id, project=project)
        logging.info("Phase 4 (Reporter) completed successfully")
        
        logging.info("All completed phases successful.")
        update_state_file(build_id, status="completed", stage="done")
        
    except Exception as e:
        logging.error(f"Sequence failed at some point: {e}")
        update_state_file(build_id, status="failed")

if __name__ == "__main__":
    main()
