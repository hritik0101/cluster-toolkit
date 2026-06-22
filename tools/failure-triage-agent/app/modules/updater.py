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

UPDATER_PROMPT = """You are the Analysis State Updater for a failure triage agent.
Your task is to merge the 'New Analysis' (from the latest investigation round) into the 'Current Analysis'.

Rules for merging:
1. "passing_signals" and "failing_signals": Keep all existing signals from the Current Analysis, and add any new signals from the New Analysis. If there is a contradiction, the New Analysis takes precedence.
2. "missing_signals": If a signal was "missing" in the Current Analysis, but is now present in the New Analysis's passing or failing signals, remove it from missing_signals.
3. "potential_root_cause_with_reason": Use the New Analysis's root cause, as it has the most up-to-date information.
4. "diagnostic_thought_process": Briefly summarize the progression of the investigation, incorporating the latest findings.
5. "evidence_logs": Combine evidence logs that support the current root cause.
6. "requested_commands": Use the commands requested in the New Analysis.
7. "hypotheses_explored": Keep all hypotheses from the Current Analysis, updating their statuses and reasoning based on the New Analysis. Add any new hypotheses introduced in the New Analysis.

OUTPUT FORMAT:
You MUST return a valid JSON object matching the exact structure of the input analyses. Do not output any Markdown wrapping or plain text outside the JSON object.
"""

def run_updater(build_id, new_analysis_json, project, location="us-central1"):
    logging.info(f"Starting LLM updater for build {build_id}")
    
    state_file = os.path.join(build_id, "state.json")
    if not os.path.exists(state_file):
        logging.error(f"State file {state_file} not found. Cannot run updater.")
        return
        
    with open(state_file, "r") as f:
        run_doc = json.load(f)

    # Clean up old rounds array if it exists (for backward compatibility during migration)
    if "rounds" in run_doc:
        del run_doc["rounds"]

    current_analysis = run_doc.get("current_analysis")
    
    if not current_analysis:
        logging.info("No current_analysis found. Setting New Analysis as the Current Analysis.")
        merged_analysis = new_analysis_json
    else:
        logging.info("Existing current_analysis found. Asking LLM to merge with the latest round...")
        # Initialize Gemini Client
        try:
            client = genai.Client(vertexai=True, project=project, location=location)
            config = types.GenerateContentConfig(
                system_instruction=[UPDATER_PROMPT],
                temperature=0.0,
                max_output_tokens=8192,
                response_mime_type="application/json"
            )
            
            prompt = f"Current Analysis:\n```json\n{json.dumps(current_analysis, indent=2)}\n```\n\nNew Analysis:\n```json\n{json.dumps(new_analysis_json, indent=2)}\n```"
            
            response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=prompt,
                config=config,
            )
            output_text = response.text
            
            clean_text = output_text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            elif clean_text.startswith("```"):
                clean_text = clean_text[3:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            
            merged_analysis = json.loads(clean_text)
            logging.info("Successfully merged analyses using LLM.")
        except Exception as e:
            logging.error(f"Failed to merge analyses with Gemini: {e}")
            logging.info("Falling back to New Analysis due to error.")
            merged_analysis = new_analysis_json

    # Write merged analysis back to state
    # We DO NOT touch commands here, as analyzer.py already handled them.
    run_doc["current_analysis"] = merged_analysis
        
    with open(state_file, "w") as f:
        json.dump(run_doc, f, indent=2)
        
    sync_state(build_id)
        
    logging.info("LLM updater completed successfully.")
