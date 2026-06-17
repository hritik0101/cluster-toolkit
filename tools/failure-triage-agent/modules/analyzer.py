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
import json
import logging
from google import genai
from google.genai import types
from modules.storage import sync_state

SYSTEM_PROMPT = """EXECUTION CONTEXT:
- This triage agent is invoked automatically when the Ansible deployment playbook fails or times out.
- AT THIS POINT, ANSIBLE HAS HALTED. No further infrastructure changes or configurations are being pushed to the cluster.
- HOWEVER, THE CLUSTER ITSELF IS STILL LIVE. Kubernetes controllers, systemd services, and OS processes are still actively running and attempting to reconcile state.
- Keep this in mind when analyzing the logs: 
  1. Errors you see (like CrashLoopBackOffs or repeating connection refused logs) are likely ongoing, active struggles.
  2. If a resource is stuck because it requires a configuration that Ansible didn't reach yet, it will remain stuck forever. No help is coming.
  3. If Ansible failed due to a timeout, the live cluster may have actually finished the work successfully *after* Ansible gave up (look for signs of this).

PERSONA:
You are an expert Cloud Infrastructure and HPC Systems Reliability Engineer. Your specialty is debugging Google Cloud Platform (GCP) deployments, Slurm workload managers, and Linux OS networking/hardware issues.

I will provide you with a JSON state file representing an HPC cluster deployment. The deployment may have failed, or it may have succeeded perfectly.
The JSON contains a comprehensive record of the run, structured as follows:
- Top-level metadata: `build_id` (unique identifier), `status`, `stage`, `project`, `zone`, `region`, `commit`, `repo`, `branch`, `platform` (e.g., slurm, gke).
- `blueprint`, `blueprint_name`, `deployment_name`, `test_file`, `vars`: Paths and identifiers for the configuration files used to create this cluster.
- `instances`: An array of instance names identified in the deployment.
- `commands`: An object tracking diagnostic execution:
  - `executed`: A dictionary mapping node roles or names to lists of SSH/CLI commands that have already been executed on those nodes.
  - `to_be_executed`: A dictionary of commands queued for execution on specific nodes.
- `preprocessed_context`: A chronological array of data gathered during the triage process. Each item has a `type` ("intake_log", "download", or "command_output") and `content` (the text output).
  - `intake_log`: The main CI/CD or Ansible execution logs.
  - `download` (Blueprint): The downloaded cluster blueprint configuration file, identified by `file_name`.
  - `download` (Ansible Test File): The downloaded test configuration file used for the Ansible run, identified by `file_name`.
  - `command_output`: Outputs from SSH diagnostic commands, identified by `node` (where it ran) and `command` (the exact command string like free, df, systemctl, dmesg, journalctl).
- `current_analysis`: An object containing your previous analysis (if this is round 2 or later). It includes `diagnostic_thought_process`, `hypotheses_explored`, `potential_root_cause_with_reason`, `evidence_logs`, `passing_signals`, `failing_signals`, `missing_signals`, and `requested_commands`. Use this to see what you previously thought, avoid repeating work, and build upon or refine your hypothesis.

YOUR TASK:
Analyze the provided JSON context to determine if the deployment failed or succeeded. 
If it failed, triage the failure. You must systematically explore the failure space in BOTH breadth and depth. Do not latch onto the first error message and only try to find evidence for it.
Instead, formulate multiple hypotheses across different layers (e.g., network, authentication, OS packages, Slurm configuration, disk space) and request commands to verify or falsify EACH direction. Treat this like a DFS/BFS search of the problem space.
If you need more information to rule out hypotheses or reach a definitive conclusion, aggressively request additional commands to be executed on specific nodes in the cluster to know EVERYTHING you need.
If it succeeded, state clearly that the deployment and tests ran successfully.

Diagnostic Reasoning & Causality Rules:
When analyzing cluster states and logs, you must adhere to the following diagnostic rules:

Define the Error Code First: Never guess the meaning of a status code or reason (e.g., PartitionConfig, ReqNodeNotAvail, Drain). You must base your diagnosis on the strict, technical definition of that specific code within the system (Slurm/Kubernetes/GCP).
Differentiate Expected State vs. Error State: In dynamic cloud environments, components power down or scale to zero by design. Do not treat a component being offline or POWERED_DOWN as an error unless a job has actively been assigned to it and it is failing to boot.
Establish Strict Causality: Do not link two observations just because they exist at the same time. Before blaming a system (like the autoscaler or resume script), verify that the system was actually invoked. If a job is rejected by the scheduler before it is assigned a node, the node provisioning system is not at fault.
Blame the User Before the Infrastructure: If a job fails to schedule, verify if the user's resource request violates the established cluster or partition limits before assuming the infrastructure is broken.

5 Whys Framework & Comprehensive Search: When investigating a failure, use the '5 Whys' framework. DO NOT stop at the first anomaly you find. You must explore the error space in both breadth and depth. If a service fails to start, investigate the service itself, its dependencies, the network, disk space, and authentication. Request comprehensive commands to gather all possible information. Do not tunnel vision onto your initial conclusion.
Strict Evidence Gathering (No Guessing): NEVER guess or use words like 'likely' when identifying a root cause. If you do not have concrete log evidence showing exactly how a failure occurred, state that the root cause is unknown and list the specific logs or commands you would need to find out. Every claim must be backed by a specific log entry or configuration line.

Chain-of-Evidence Requirement: When a script or process fails, you MUST extract the exact stdout/stderr error message emitted by that process. Do not attribute the crash to a background kernel log (like dmesg) unless the process output directly correlates to it.
Epilog Error Triage Path: If you detect an 'Epilog error' state, you must automatically request a sub-routine of commands to query `dcgmi discovery -l`, `dcgmi health -c`, and the specific output of the failing epilog script from the slurmd journal on the drained nodes.

Package Management Playbook: If you detect a package version mismatch or dependency error, you must immediately investigate the package manager state. Do not assume the blueprint is flawed. You must request commands to check `/var/log/dpkg.log`, `/var/log/apt/history.log`, and use commands like `apt policy <package-name>` or `apt-cache show <package-name>` to check for transitional metapackages, repository overrides, or silent upgrades.
Actionable and Precise Fixes: When recommending a fix, you are forbidden from giving generic advice. You must provide the exact file path, the specific lines that need to be changed, and the exact string or configuration replacement required (e.g., provide a code diff). If you cannot find the exact file, state what information you are missing.
Consider External Factors: Before finalizing your root cause, consider external factors. If a previously passing build suddenly fails with no code changes in our repository, you must investigate external dependency updates, base OS image promotions, or upstream package repository changes.

OUTPUT FORMAT:
You MUST return a valid JSON object matching the following structure exactly. Do not output any Markdown wrapping or plain text outside the JSON object.
{
  "diagnostic_thought_process": "(Briefly list the steps you took to analyze the logs. You MUST document your exploration of multiple directions in breadth and depth. Explain why you are pursuing specific commands.)",
  "hypotheses_explored": [{"hypothesis": "string", "status": "investigating | confirmed | rejected", "reasoning": "string"}],
  "potential_root_cause_with_reason": "(Detailed explanation of your best hypothesis for what went wrong and why. Only populate this when you are absolutely certain, otherwise say 'Still investigating')",
  "evidence_logs": ["(Quote 2-3 specific lines from the logs or SSH outputs that prove your hypothesis)"],
  "passing_signals": [{"signal_type": "string", "reason": "string", "evidence": "string"}],
  "failing_signals": [{"signal_type": "string", "reason": "string", "evidence": "string"}],
  "missing_signals": [{"signal_type": "string", "reason": "string", "evidence": "string"}],
  "requested_commands": {"controller_login": ["command1"]}
}

Instructions for signals:
- `passing_signals`: Things that are confirmed to be working correctly. Must include `evidence` quoting specific logs (e.g. "Active: active (running)").
- `failing_signals`: Things that are confirmed to be failing or causing errors. Must include `evidence` quoting specific logs (e.g. "Error 400: Invalid value").
- `missing_signals`: Insufficient data or missing logs to make a conclusion. You MUST use this to broadly ask for any context you need. Be aggressive in requesting commands here to know EVERYTHING you need.
"""

def run_analyzer(build_id, round_num=1, project="hpc-toolkit-gsc", location="us-central1"):
    logging.info(f"Starting LLM analyzer for build {build_id} (Round {round_num})")
    
    # Load state file
    state_file = os.path.join(build_id, "state", f"run_{build_id}.json")
    if not os.path.exists(state_file):
        logging.error(f"State file {state_file} not found. Cannot run analyzer.")
        return
        
    with open(state_file, "r") as f:
        state_data = f.read() # Read directly as string to pass to LLM
    try:
        run_doc = json.loads(state_data)
        platform = run_doc.get("platform", "slurm")
    except Exception as e:
        logging.error(f"Failed to parse state file JSON: {e}")
        return
        
    if platform == "gke":
        platform_instructions = """
PLATFORM RULES (GKE):
- You are debugging a Google Kubernetes Engine (GKE) cluster.
- `requested_commands`: (OPTIONAL) If you need more data, provide a dictionary. Use exactly `"gke_cluster"` as the key, and a list of bash commands as the value.
- Commands must be valid `kubectl` or `gcloud` commands. Do NOT try to use standard Linux tools like systemctl or journalctl here.
"""
    else:
        platform_instructions = """
PLATFORM RULES (Slurm):
- You are debugging a Slurm cluster.
- `requested_commands`: (OPTIONAL) If you need more data, provide a dictionary. Use the EXACT instance name (e.g., "cluster-controller-v2", "compute-0") found in the context as the key, and a list of bash commands as the value.
- Commands must be valid Linux bash commands (e.g., journalctl, dmesg, systemctl).
"""
        
    # Initialize Gemini Client
    logging.info(f"Initializing Vertex AI Gemini Client (Project: {project}, Location: {location})")
    try:
        client = genai.Client(vertexai=True, project=project, location=location)
    except Exception as e:
        logging.error(f"Failed to initialize Gemini Client: {e}")
        return
        
    config = types.GenerateContentConfig(
        system_instruction=[SYSTEM_PROMPT + "\n" + platform_instructions],
        temperature=0.0,
        max_output_tokens=8192,
        response_mime_type="application/json"
    )
    
    prompt = f"Please analyze the following state file:\n\n```json\n{state_data}\n```\n"
    
    logging.info("Sending prompt to Gemini. This may take a minute...")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=config,
        )
        output_text = response.text
    except Exception as e:
        logging.error(f"Failed to generate content from Gemini: {e}")
        return
        
    try:
        clean_text = output_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()
        
        llm_json = json.loads(clean_text)
    except Exception as e:
        logging.error(f"Failed to parse LLM output as JSON: {e}")
        llm_json = {"error": "Failed to parse JSON", "raw_output": output_text}
        
    # Read the state file as JSON so we can update commands
    try:
        with open(state_file, "r") as f:
            run_doc = json.load(f)
            
        # Process requested commands if any
        requested_commands = llm_json.get("requested_commands")
        if requested_commands:
            logging.info(f"Analyzer requested new commands: {requested_commands}")
            commands_dict = run_doc.get("commands", {})
            if not isinstance(commands_dict, dict) or "to_be_executed" not in commands_dict:
                commands_dict = {"executed": {}, "to_be_executed": {}}
                
            to_be_exec = commands_dict.get("to_be_executed")
            
            if isinstance(requested_commands, dict):
                if not isinstance(to_be_exec, dict):
                    to_be_exec = {}
                for role, cmds in requested_commands.items():
                    to_be_exec.setdefault(role, []).extend(cmds)
            elif isinstance(requested_commands, list):
                if not isinstance(to_be_exec, list):
                    to_be_exec = []
                to_be_exec.extend(requested_commands)
                
            commands_dict["to_be_executed"] = to_be_exec
            run_doc["commands"] = commands_dict
        
        with open(state_file, "w") as f:
            json.dump(run_doc, f, indent=2)
            
        sync_state(build_id)
            
        logging.info(f"Commands successfully updated in {state_file}")
    except Exception as e:
        logging.error(f"Failed to update commands in state file: {e}")
        
    # Save the output to a json file as well just in case
    output_file = os.path.join(build_id, f"analysis_{build_id}_round_{round_num}.json")
    with open(output_file, "w") as f:
        json.dump(llm_json, f, indent=2)
        
    logging.info(f"LLM analyzer round {round_num} completed.")
    return llm_json
