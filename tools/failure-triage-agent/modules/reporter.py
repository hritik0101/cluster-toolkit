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

SYSTEM_PROMPT = """EXECUTION CONTEXT:
- This triage agent is invoked automatically when the Ansible deployment playbook fails or times out.
- AT THIS POINT, ANSIBLE HAS HALTED. No further infrastructure changes or configurations are being pushed to the cluster.
- HOWEVER, THE CLUSTER ITSELF IS STILL LIVE. Kubernetes controllers, systemd services, and OS processes are still actively running and attempting to reconcile state.
- Keep this in mind when analyzing the logs: 
  1. Errors you see (like CrashLoopBackOffs or repeating connection refused logs) are likely ongoing, active struggles.
  2. If a resource is stuck because it requires a configuration that Ansible didn't reach yet, it will remain stuck forever. No help is coming.
  3. If Ansible failed due to a timeout, the live cluster may have actually finished the work successfully *after* Ansible gave up (look for signs of this).

PERSONA:
You are a Technical Writer and Site Reliability Engineer, taking a holistic, high-level perspective to make the final determination and summarize the investigation into a cohesive Markdown report for the engineering team.

I will provide you with the full state file of an automated triage agent's investigation. 
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
- `current_analysis`: The final merged analytical thoughts of the triage agent. It includes `diagnostic_thought_process`, `potential_root_cause_with_reason`, `evidence_logs`, `passing_signals`, `failing_signals`, and `missing_signals`.

YOUR TASK:
Synthesize this entire investigation into a cohesive Markdown report. 
Trace the agent's logic from the initial failure to the final conclusion, taking a holistic, high-level perspective to make the final result. Pull out the most critical log snippets from the 'preprocessed_context' and the 'evidence_logs' to use as evidence.
Generate a Markdown report that includes the following sections:
1. Executive Summary: A high-level 1-2 sentence summary of what happened.
2. Root Cause: A summary of the potential root cause identified by the agent.
3. Recommended Fix: Actionable steps to resolve the issue based on the root cause.
4. Evidence Logs: A section showing the specific failing signals or logs that led to this conclusion. Format these as code blocks.

OUTPUT FORMAT:
Return ONLY valid Markdown text. Do not wrap it in JSON. Start directly with the `# Triage Report` heading.
"""

def run_reporter(build_id, project_id="hpc-toolkit-gsc", location="us-central1"):
    logging.info(f"Starting LLM Reporter for build {build_id}")
    
    # Load state file
    state_file = os.path.join(build_id, "state", f"run_{build_id}.json")
    if not os.path.exists(state_file):
        logging.error(f"State file {state_file} not found. Cannot run reporter.")
        return
        
    try:
        with open(state_file, "r") as f:
            state_data_str = f.read()
    except Exception as e:
        logging.error(f"Failed to read state file: {e}")
        return
    
    # Initialize Gemini Client
    logging.info(f"Initializing Vertex AI Gemini Client (Project: {project_id}, Location: {location})")
    try:
        client = genai.Client(vertexai=True, project=project_id, location=location)
    except Exception as e:
        logging.error(f"Failed to initialize Gemini Client: {e}")
        return
        
    config = types.GenerateContentConfig(
        system_instruction=[SYSTEM_PROMPT],
        temperature=0.2,
        max_output_tokens=8192,
        response_mime_type="text/plain"
    )
    
    prompt = f"Please write the final Markdown report based on the following diagnostic data state file:\n\n```json\n{state_data_str}\n```\n"
    
    logging.info("Sending prompt to Gemini for report generation...")
    try:
        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=prompt,
            config=config,
        )
        report_text = response.text
    except Exception as e:
        logging.error(f"Failed to generate report from Gemini: {e}")
        return
        
    # Clean up any potential markdown code blocks around the entire output
    clean_text = report_text.strip()
    if clean_text.startswith("```markdown"):
        clean_text = clean_text[11:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    # Save the output to a markdown file
    output_file = os.path.join(build_id, f"report_{build_id}.md")
    try:
        with open(output_file, "w") as f:
            f.write(clean_text)
        logging.info(f"Markdown report saved to {output_file}")
    except Exception as e:
        logging.error(f"Failed to save markdown report: {e}")
        
    # Print to stdout
    print("\n\n" + "="*60)
    print(f"      FINAL TRIAGE REPORT FOR BUILD {build_id}      ")
    print("="*60)
    print(clean_text)
    print("="*60 + "\n\n")
