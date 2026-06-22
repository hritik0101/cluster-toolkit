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
from modules.storage import upload_report, BUCKET_NAME

SYSTEM_PROMPT = """EXECUTION CONTEXT:
- This triage agent is invoked automatically when the Ansible deployment playbook fails or times out.
- AT THIS POINT, ANSIBLE HAS HALTED. No further infrastructure changes or configurations are being pushed to the cluster.
- HOWEVER, THE CLUSTER ITSELF IS STILL LIVE. Kubernetes controllers, systemd services, and OS processes are still actively running and attempting to reconcile state.
- Keep this in mind when analyzing the logs: 
  1. Errors you see (like CrashLoopBackOffs or repeating connection refused logs) are likely ongoing, active struggles.
  2. If a resource is stuck because it requires a configuration that Ansible didn't reach yet, it will remain stuck forever. No help is coming.
  3. If Ansible failed due to a timeout, the live cluster may have actually finished the work successfully *after* Ansible gave up (look for signs of this).

PERSONA:
You are a Technical Writer and Site Reliability Engineer, taking a holistic, high-level perspective to make the final determination and summarize the investigation into a cohesive plain text report for the engineering team.

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
- `current_analysis`: The final merged analytical thoughts of the triage agent. It includes `diagnostic_thought_process`, `hypotheses_explored`, `potential_root_cause_with_reason`, `evidence_logs`, `passing_signals`, `failing_signals`, and `missing_signals`.

YOUR TASK:
Synthesize this entire investigation into a cohesive plain text report. THESE ARE NOT SUGGESTIONS. THESE ARE STRICT COMMANDS YOU MUST FOLLOW.

SCALE VERBOSITY TO COMPLEXITY RULE:
- SUCCESS PATH: If the state file indicates the deployment succeeded, output ONLY an 'EXECUTIVE SUMMARY' confirming success and omit all other sections entirely.
- MINOR/SIMPLE FAILURE PATH: Treat the problem as minor if the root cause is a single-layer issue (e.g., an API quota error, a syntax typo in the blueprint, missing credentials). For these, keep the report extremely brief. Explain the error and the fix directly in a few sentences, and skip the long forensic chronology.
- COMPLEX/DEEP FAILURE PATH: Treat the problem as complex ONLY if the root cause involves a multi-layer chain of causality (e.g., Service A failed because Process B crashed, which was caused by a silent OS package upgrade of Dependency C). For these, provide the detailed 'Chronology' explaining the full "5 Whys" chain.

ANTI-REPETITION RULE:
- You are strictly forbidden from repeating the same narrative or error message across sections.
- The 'Executive Summary' is the standalone TL;DR.
- The 'Potential Root Cause' explains the mechanical How/Why. Do NOT re-summarize the TL;DR here.
- The 'Recommended Remediation' provides the fix. Do NOT re-explain why it broke here.
- The 'Evidence & Diagnostic Logs' provides raw logs and a 1-sentence explanation. Do NOT recount the narrative here.

Generate a plain text report that includes the following sections. You MUST use exactly these section titles in ALL CAPS:

EXECUTIVE SUMMARY
Provide a concise but highly technical 1-2 sentence summary of the incident. It must immediately answer: What failed, what was the business impact (e.g., nodes drained, tests timed out), and what was the high-level technical cause. Include a short disclaimer that this is an AI-generated analysis.

CHRONOLOGY OF EVENTS & POTENTIAL ROOT CAUSE
Provide a summary of the potential root cause. Always frame your conclusions as the *potential* root cause, as this is an automated analysis. Do not state assumptions as absolute facts. Explain the exact mechanism of failure. If the problem is complex, detail the entire chain of causality (the '5 Whys'). If the failure involves external systems (OS image promotions, package managers, dependency conflicts), explain how those external factors interacted with our infrastructure code.

RECOMMENDED REMEDIATION
Actionable, exact steps to resolve the issue based on the potential root cause. You are forbidden from giving generic advice like "Fix the configuration" or "Ensure versions match". You MUST provide the exact file path and the specific code or configuration change required to fix the issue. 
- ANTI-HALLUCINATION RULE: DO NOT guess or hallucinate mappings between platform versions, hardware types, or OS images. If the exact mapping is not proven, state that the user must consult official documentation.
- RESOURCE CAPACITY RULE: If resource exhaustion occurred, recommend modifying the blueprint to increase capacity, not "cleaning up stale resources."
- LIFECYCLE MANAGEMENT RULE: For unmanaged dependencies, recommend pinning. For Managed Services, explicitly FORBID pinning exact patch versions.
- PREFIX BEHAVIOR RULE: Do not hallucinate that strict version prefixes are "ambiguous" or jump major/minor boundaries on their own. 
- BLAST RADIUS & MASKED FAILURES: Include a subsection analyzing if this vulnerability affects other blueprints in the repository, and explicitly note if any flawed testing commands masked secondary failures.

EVIDENCE & DIAGNOSTIC LOGS
A curated section showing the specific failing signals or logs that led to this conclusion. Do not just dump logs. Introduce each log block by explaining exactly what it proves in one sentence. Ensure you establish a strict chain-of-evidence: if attributing a process crash to a kernel log, verify that the process's own stdout/stderr error message directly correlates to it.

Plain Text Formatting Rules:
- DO NOT use markdown formatting (no # for headers, no backticks `).
- Use ALL CAPS for section headers and underline them with equals signs (e.g., =======).
- Use asterisks (*) or hyphens (-) for bulleted lists.
- Separate major sections with a line of dashes (----------------------------------------).
- Use clear spacing between paragraphs.

OUTPUT FORMAT:
Return ONLY formatted plain text. Do not wrap it in JSON. Start directly with the TRIAGE REPORT heading.
"""

def run_reporter(build_id, project="hpc-toolkit-gsc", location="us-central1"):
    logging.info(f"Starting LLM Reporter for build {build_id}")
    
    # Load state file
    state_file = os.path.join(build_id, "state.json")
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
    logging.info(f"Initializing Vertex AI Gemini Client (Project: {project}, Location: {location})")
    try:
        client = genai.Client(vertexai=True, project=project, location=location)
    except Exception as e:
        logging.error(f"Failed to initialize Gemini Client: {e}")
        return
        
    config = types.GenerateContentConfig(
        system_instruction=[SYSTEM_PROMPT],
        temperature=0.2,
        max_output_tokens=8192,
        response_mime_type="text/plain"
    )
    
    prompt = f"Please write the final text report based on the following diagnostic data state file:\n\n```json\n{state_data_str}\n```\n"
    
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
    if clean_text.startswith("```text"):
        clean_text = clean_text[7:]
    elif clean_text.startswith("```"):
        clean_text = clean_text[3:]
    if clean_text.endswith("```"):
        clean_text = clean_text[:-3]
    clean_text = clean_text.strip()
    
    # Extract the Executive Summary
    summary_lines = []
    in_summary = False
    HEADERS = {"CHRONOLOGY OF EVENTS", "RECOMMENDED REMEDIATION", "EVIDENCE"}
    for line in clean_text.split('\n'):
        if line.strip() == "EXECUTIVE SUMMARY":
            in_summary = True
            continue
        if in_summary:
            # Break if we hit the next major header
            if any(line.strip().startswith(h) for h in HEADERS):
                break
            if line.strip():
                summary_lines.append(line.strip())
    
    extracted_summary = " ".join(summary_lines)
    if not extracted_summary:
        extracted_summary = "Could not extract executive summary. See full report."
    
    # Save the output to a plain text file
    output_file = os.path.join(build_id, f"report_{build_id}.txt")
    try:
        with open(output_file, "w") as f:
            f.write(clean_text)
        logging.info(f"Plain text report saved to {output_file}")
        upload_report(build_id)
    except Exception as e:
        logging.error(f"Failed to save text report: {e}")

    # Update executive_summary in state.json
    state_file = os.path.join(build_id, "state.json")
    try:
        with open(state_file, "r") as f:
            state_data = json.load(f)
        state_data["executive_summary"] = extracted_summary
        with open(state_file, "w") as f:
            json.dump(state_data, f, indent=2)
        logging.info("Updated executive_summary in state.json")
    except Exception as e:
        logging.error(f"Failed to update executive summary in state: {e}")
        
    # Print to stdout
    print("\n\n" + "="*60)
    print(f"      TRIAGE EXECUTIVE SUMMARY FOR BUILD {build_id}      ")
    print("="*60)
    print(extracted_summary)
    print("="*60)
    print(f"Full forensic report available at: https://storage.cloud.google.com/{BUCKET_NAME}/{build_id}/report.txt\n\n")
