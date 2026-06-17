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
            
        # Call the trigger script
        print(f"Calling trigger.py for build_id={build_id}, project_id={project_id}")
        result = subprocess.run(
            [sys.executable, "trigger.py", "--build-id", build_id, "--project-id", project_id],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            print(f"trigger.py failed with returncode {result.returncode}")
            print(f"stdout: {result.stdout}")
            print(f"stderr: {result.stderr}")
            
            # If the subprocess fails due to an IAM / Permission Denied error, return 403
            if any(term in result.stderr for term in ["403", "Forbidden", "PermissionDenied", "AccessDenied"]):
                return jsonify({
                    "error": "IAM Permission Denied",
                    "details": result.stderr,
                    "stdout": result.stdout
                }), 403

            return jsonify({
                "error": "Trigger script failed",
                "details": result.stderr,
                "stdout": result.stdout
            }), 500
        
        # Mock result for demonstration
        result_message = f"Successfully processed {build_id} and {project_id}\nOutput:\n{result.stdout}"
        
        return jsonify({"status": "success", "result": result_message}), 200
        
    except Exception as e:
        error_msg = str(e)
        if any(term in error_msg for term in ["403", "Forbidden", "PermissionDenied", "AccessDenied"]):
            return jsonify({"error": error_msg}), 403
        return jsonify({"error": error_msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
