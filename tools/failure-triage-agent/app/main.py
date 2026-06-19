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
import sys
import subprocess
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/trigger', methods=['POST'])
def trigger_process():
    try:
        data = request.get_json()
        
        # Extract the 2 arguments from the request body
        build_id = data.get('build_id')
        project_id = data.get('project_id')
        
        if not build_id or not project_id:
            return jsonify({"error": "Missing build_id or project_id"}), 400
            
        # Call the trigger script in the background
        print(f"Calling trigger.py for build_id={build_id}, project_id={project_id}")
        process = subprocess.Popen(
            [sys.executable, "-u", "trigger.py", "--build-id", build_id, "--project-id", project_id]
        )
        
        try:
            # Wait up to 3 seconds to catch immediate crashes (e.g. IAM or missing bucket)
            returncode = process.wait(timeout=3)
            
            if returncode != 0:
                return jsonify({
                    "error": "Trigger script failed immediately", 
                    "details": f"Exit code {returncode}. Check Cloud Logging for full traceback."
                }), 500
            else:
                return jsonify({"status": "success", "message": "Finished successfully"}), 200
                
        except subprocess.TimeoutExpired:
            # Still running after 3 seconds, meaning it passed initial checks!
            return jsonify({"status": "success", "message": "Pipeline started in background"}), 202
        
    except Exception as e:
        error_msg = str(e)
        if any(term in error_msg for term in ["403", "Forbidden", "PermissionDenied", "AccessDenied"]):
            return jsonify({"error": error_msg}), 403
        return jsonify({"error": error_msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
