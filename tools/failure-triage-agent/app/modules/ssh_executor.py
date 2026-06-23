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
import subprocess
import getpass
import shlex
import sys
import os
from modules.log_filter import process_in_memory
from modules.verifier import is_command_safe
from modules.gcs_client import sync_state

logger = logging.getLogger("ssh_executor")

def get_oslogin_username() -> str:
    """Retrieves the OS Login username for the active gcloud profile, falling back to local user."""
    try:
        logger.debug("Running gcloud compute os-login describe-profile...")
        res = subprocess.run(
            ["gcloud", "compute", "os-login", "describe-profile", "--format=json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60
        )
        profile = json.loads(res.stdout)
        for account in profile.get("posixAccounts", []):
            if account.get("username"):
                username = account["username"]
                logger.info(f"Retrieved OS Login username: {username}")
                return username
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not retrieve OS Login username: {e}. STDERR: {e.stderr}. Falling back to local username.")
    except Exception as e:
        logger.warning(f"Could not retrieve OS Login username: {e}. Falling back to local username.")
    
    local_user = getpass.getuser()
    logger.info(f"Using fallback username: {local_user}")
    return local_user

def list_deployment_instances(project: str, deployment_name: str) -> list:
    """Lists GCE instances matching the deployment name label."""
    logger.info(f"Listing GCE instances with label ghpc_deployment={deployment_name} in project {project}")
    cmd = [
        "gcloud", "compute", "instances", "list",
        f"--project={project}",
        f"--filter=labels.ghpc_deployment={deployment_name}",
        "--format=json(name,zone,status)"
    ]
    try:
        logger.debug(f"Running command: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
        instances = json.loads(res.stdout)
        
        # Normalize zones (extract zone name from the full URL if present)
        for inst in instances:
            if "zone" in inst and "/" in inst["zone"]:
                inst["zone"] = inst["zone"].split("/")[-1]
                
        logger.info(f"Found {len(instances)} instances for deployment {deployment_name}")
        for inst in instances:
            logger.debug(f"Instance: {inst['name']} | Zone: {inst['zone']} | Status: {inst['status']}")
            
        return instances
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to list GCE instances: {e}. STDERR: {e.stderr}")
        return []
    except Exception as e:
        logger.error(f"Failed to list GCE instances: {e}")
        return []

def run_ssh_executor(build_id: str):
    """Main execution function for Module 2 (SSH Executor)."""
    # Configure file logger for the ssh_executor module
    for h in logger.handlers[:]:
        logger.removeHandler(h)
        
    IS_CLOUD_RUN = os.environ.get('CLOUD_RUN') == 'true'
    if IS_CLOUD_RUN:
        logger.propagate = True
        logger.setLevel(logging.DEBUG)
    else:
        log_file = os.path.join(build_id, "logs", f"ssh_executor_{build_id}.log")
        file_handler = logging.FileHandler(log_file, mode='a')
        stream_handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s - %(message)s')
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

    logger.info(f"Starting SSH Executor Phase for Build ID: {build_id}")
    
    # 1. Read the run document JSON
    
    run_file = os.path.join(build_id, "state.json")
    logger.debug(f"Reading run document from {run_file}")
    try:
        with open(run_file, "r") as f:
            run_doc = json.load(f)
    except Exception as e:
        logger.error(f"Failed to read run document {run_file}: {e}")
        raise

    save_preprocessed = run_doc.get("save_preprocessed", False)
    
    if save_preprocessed:
        preprocessed_dir = os.path.join(build_id, "preprocessed")
        os.makedirs(preprocessed_dir, exist_ok=True)
        clean_ssh_file = os.path.join(preprocessed_dir, "clean_ssh.txt")
        # Clear the file if it exists, since we append sequentially
        open(clean_ssh_file, 'w').close()
    else:
        clean_ssh_file = None

    platform = run_doc.get("platform")
    project = run_doc.get("project")
    deployment_name = run_doc.get("deployment_name")
    commands_dict = run_doc.get("commands", {})
    if not isinstance(commands_dict, dict) or "to_be_executed" not in commands_dict:
        commands_dict = {"executed": {} if isinstance(run_doc.get("command_manifest", []), dict) else [], "to_be_executed": run_doc.get("command_manifest", [])}
    
    command_manifest = commands_dict.get("to_be_executed", [])
    save_raw = run_doc.get("save_raw", True)

    logger.info(f"Platform: {platform} | Project: {project} | Deployment: {deployment_name}")
    logger.info(f"Command Manifest: {command_manifest}")
    
    if not command_manifest:
        logger.info("No commands to be executed.")
        return

    if not project or not deployment_name:
        error_msg = f"Missing project or deployment name in run document for Build ID {build_id}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    # 2. Get OS Login Username
    username = get_oslogin_username()

    def finalize_commands():
        # Move all to_be_executed to executed
        if isinstance(commands_dict.get("to_be_executed"), dict):
            if "executed" not in commands_dict:
                commands_dict["executed"] = {}
            elif not isinstance(commands_dict["executed"], dict):
                # Preserve existing list history under a generic key
                existing_list = commands_dict["executed"] if isinstance(commands_dict["executed"], list) else []
                commands_dict["executed"] = {"previous_list_commands": existing_list} if existing_list else {}
                
            for role, cmds in commands_dict["to_be_executed"].items():
                if isinstance(cmds, list):
                    commands_dict["executed"].setdefault(role, []).extend(cmds)
            commands_dict["to_be_executed"] = {}
            
        elif isinstance(commands_dict.get("to_be_executed"), list):
            if "executed" not in commands_dict:
                commands_dict["executed"] = []
            elif not isinstance(commands_dict["executed"], list):
                # Preserve existing dict history by flattening values
                existing_dict = commands_dict["executed"] if isinstance(commands_dict["executed"], dict) else {}
                flat_list = []
                for cmds in existing_dict.values():
                    if isinstance(cmds, list):
                        flat_list.extend(cmds)
                commands_dict["executed"] = flat_list
                
            commands_dict["executed"].extend(commands_dict["to_be_executed"])
            commands_dict["to_be_executed"] = []
            
        run_doc["commands"] = commands_dict
        with open(run_file, "w") as f:
            json.dump(run_doc, f, indent=2)
        sync_state(build_id)

    if platform == "gke":
        logger.info(f"Platform is GKE. Identifying cluster location for {deployment_name}...")
        try:
            res = subprocess.run([
                "gcloud", "container", "clusters", "list",
                f"--project={project}",
                f"--filter=name:{deployment_name}",
                "--format=value(location)"
            ], capture_output=True, text=True, check=True, timeout=60)
            location = res.stdout.strip()
            if not location:
                logger.warning(f"Could not find GKE cluster named {deployment_name}")
                finalize_commands()
                return
                
            logger.info(f"Found cluster at location: {location}.")
            logger.info("Fetching credentials...")
            subprocess.run([
                "gcloud", "container", "clusters", "get-credentials", deployment_name,
                f"--project={project}",
                f"--location={location}"
            ], check=True, capture_output=True, timeout=60)
            
            logger.info("Fetching nodepools...")
            try:
                np_res = subprocess.run([
                    "gcloud", "container", "node-pools", "list",
                    f"--cluster={deployment_name}",
                    f"--location={location}",
                    f"--project={project}",
                    "--format=value(name)"
                ], capture_output=True, text=True, check=True, timeout=60)
                nodepools = [np.strip() for np in np_res.stdout.strip().split('\n') if np.strip()]
                logger.info(f"Found nodepools: {nodepools}")
                run_doc["nodepools"] = nodepools
                with open(run_file, "w") as f:
                    json.dump(run_doc, f, indent=2)
                sync_state(build_id)
            except Exception as e:
                logger.error(f"Failed to fetch nodepools: {e}")
            
            cmds_to_run = []
            if isinstance(command_manifest, dict):
                cmds_to_run = command_manifest.get("gke_cluster", [])
            elif isinstance(command_manifest, list):
                cmds_to_run = command_manifest
                
            for i, cmd in enumerate(cmds_to_run):
                if not is_command_safe(cmd):
                    logger.warning(f"Skipping dangerous command: {cmd}")
                    continue
                logger.info(f"Executing GKE API command: {cmd}")
                try:
                    try:
                        cmd_res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
                        stdout, stderr = cmd_res.stdout, cmd_res.stderr
                    except subprocess.TimeoutExpired as e:
                        logger.error(f"Command '{cmd}' timed out after 60 seconds.")
                        stdout = e.stdout or ""
                        stderr = f"TimeoutExpired: Command timed out after 60 seconds.\n{e.stderr or ''}"

                    if save_raw:
                        os.makedirs(os.path.join(build_id, "ssh_output"), exist_ok=True)
                        output_file = os.path.join(build_id, "ssh_output", f"gke_cluster_cmd{i}.txt")
                        with open(output_file, "w") as out_f:
                            out_f.write(f"Command: {cmd}\n")
                            out_f.write(f"STDOUT:\n{stdout}\n")
                            out_f.write(f"STDERR:\n{stderr}\n")
                        logger.info(f"Saved command output to {output_file}")
                    
                    combined_output = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n"
                    cleaned = process_in_memory(combined_output, 'command')
                    if save_preprocessed and clean_ssh_file:
                        with open(clean_ssh_file, "a") as f:
                            f.write(f"=== Node: gke_cluster ===\n> Command: {cmd}\n{cleaned}\n\n")
                            
                    run_doc.setdefault("preprocessed_context", []).append({
                        "type": "command_output",
                        "node": "gke_cluster",
                        "command": cmd,
                        "content": cleaned
                    })
                    
                    with open(run_file, "w") as f:
                        json.dump(run_doc, f, indent=2)
                    sync_state(build_id)
                except Exception as e:
                    logger.error(f"Failed to execute command '{cmd}': {e}")
                    
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to interact with GKE cluster: {e}. STDERR: {e.stderr}")
        except Exception as e:
            logger.error(f"Failed to interact with GKE cluster: {e}")
            
    else:
        # 3. Get instances matching the deployment
        instances = list_deployment_instances(project, deployment_name)
        if not instances:
            logger.warning(f"No GCE instances found for deployment {deployment_name}")
            finalize_commands()
            return
            
        instance_names = [inst["name"] for inst in instances]
        logger.info(f"Found instances: {instance_names}")
        run_doc["instances"] = instance_names
        with open(run_file, "w") as f:
            json.dump(run_doc, f, indent=2)
        sync_state(build_id)
    
        # 4. Construct connection commands accordingly
        logger.info("Constructing connection commands...")
        for inst in instances:
            instance_name = inst["name"]
            zone = inst["zone"]
            status = inst["status"]
    
            # Check platform or instance type to make connection commands
            logger.info(f"Processing node {instance_name} ({platform} node, status: {status})")
    
            # Basic gcloud SSH command template
            connection_base = [
                "gcloud", "compute", "ssh",
                f"{username}@{instance_name}",
                f"--project={project}",
                f"--zone={zone}",
                "--tunnel-through-iap"
            ]
    
            logger.debug(f"Connection base command: {' '.join(connection_base)}")
    
            # Construct and execute specific commands for the manifest
            node_commands = []
            if isinstance(command_manifest, dict):
                if instance_name in command_manifest:
                    node_commands = command_manifest.get(instance_name, [])
                elif "controller" in instance_name:
                    node_commands = command_manifest.get("controller_login", [])
                elif "nodeset" in instance_name:
                    node_commands = command_manifest.get("nodeset", [])
                else:
                    logger.warning(f"Could not determine role for {instance_name}, skipping commands.")

            for i, cmd in enumerate(node_commands):
                if not is_command_safe(cmd):
                    logger.warning(f"Skipping dangerous command on {instance_name}: {cmd}")
                    continue
                ssh_command = connection_base + ["--command", cmd]
                logger.info(f"Executing command on {instance_name}: {' '.join(ssh_command)}")
                try:
                    try:
                        res = subprocess.run(ssh_command, capture_output=True, text=True, timeout=60)
                        stdout, stderr = res.stdout, res.stderr
                    except subprocess.TimeoutExpired as e:
                        logger.error(f"SSH command on {instance_name} timed out after 60 seconds: {cmd}")
                        stdout = e.stdout or ""
                        stderr = f"TimeoutExpired: SSH command timed out after 60 seconds.\n{e.stderr or ''}"

                    if save_raw:
                        instance_dir = os.path.join(build_id, "ssh_output", instance_name)
                        os.makedirs(instance_dir, exist_ok=True)
                        output_file = os.path.join(instance_dir, f"cmd{i}.txt")
                        with open(output_file, "w") as out_f:
                            out_f.write(f"Command: {cmd}\n")
                            out_f.write(f"STDOUT:\n{stdout}\n")
                            out_f.write(f"STDERR:\n{stderr}\n")
                        logger.info(f"Saved ssh output to {output_file}")
                    
                    combined_output = f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}\n"
                    cleaned = process_in_memory(combined_output, 'command')
                    if save_preprocessed and clean_ssh_file:
                        with open(clean_ssh_file, "a") as f:
                            f.write(f"=== Node: {instance_name} ===\n> Command: {cmd}\n{cleaned}\n\n")
                            
                    run_doc.setdefault("preprocessed_context", []).append({
                        "type": "command_output",
                        "node": instance_name,
                        "command": cmd,
                        "content": cleaned
                    })
                    
                    needs_auto_fetch = False
                    if "Epilog error" in cleaned or "epilog failed" in cleaned.lower():
                        needs_auto_fetch = True
                    elif "failed" in cleaned.lower() and "service" in cleaned.lower() and "systemctl" in cmd:
                        needs_auto_fetch = True
                    
                    if needs_auto_fetch:
                        logger.info(f"Detected failure signs on {instance_name}, fetching additional logs automatically.")
                        extra_cmds = [
                            "journalctl -u slurmd -n 200 --no-pager",
                            "journalctl -u nvidia-dcgm -n 200 --no-pager"
                        ]
                        for extra_cmd in extra_cmds:
                            logger.info(f"Auto-executing extra command on {instance_name}: {extra_cmd}")
                            try:
                                e_res = subprocess.run(connection_base + ["--command", extra_cmd], capture_output=True, text=True, timeout=60)
                                e_out = f"STDOUT:\n{e_res.stdout}\nSTDERR:\n{e_res.stderr}\n"
                                e_cleaned = process_in_memory(e_out, 'command')
                                if save_preprocessed and clean_ssh_file:
                                    with open(clean_ssh_file, "a") as f:
                                        f.write(f"=== Node: {instance_name} ===\n> Command: {extra_cmd} (AUTO)\n{e_cleaned}\n\n")
                                run_doc.setdefault("preprocessed_context", []).append({
                                    "type": "command_output",
                                    "node": instance_name,
                                    "command": extra_cmd,
                                    "content": e_cleaned
                                })
                            except Exception as e:
                                logger.error(f"Failed auto-executing {extra_cmd} on {instance_name}: {e}")

                    with open(run_file, "w") as f:
                        json.dump(run_doc, f, indent=2)
                    sync_state(build_id)
                except Exception as e:
                    logger.error(f"Failed to execute SSH command on {instance_name}: {e}")

    finalize_commands()

    logger.info("SSH Executor Phase completed successfully")
