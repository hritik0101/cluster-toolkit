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
        process = subprocess.Popen(
            [sys.executable, "-u", "trigger.py", "--build-id", build_id, "--project-id", project_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        captured_output = []
        for line in iter(process.stdout.readline, ''):
            print(line, end='', flush=True)
            captured_output.append(line)
            
        process.stdout.close()
        returncode = process.wait()
        
        full_output = ''.join(captured_output)
        
        if returncode != 0:
            print(f"trigger.py failed with returncode {returncode}")
            
            # If the subprocess fails due to an IAM / Permission Denied error, return 403
            if any(term in full_output for term in ["403", "Forbidden", "PermissionDenied", "AccessDenied"]):
                return jsonify({
                    "error": "IAM Permission Denied",
                    "details": full_output,
                    "stdout": ""
                }), 403

            return jsonify({
                "error": "Trigger script failed",
                "details": full_output,
                "stdout": ""
            }), 500
        
        # Mock result for demonstration
        result_message = f"Successfully processed {build_id} and {project_id}"
        
        return jsonify({"status": "success", "result": result_message}), 200
        
    except Exception as e:
        error_msg = str(e)
        if any(term in error_msg for term in ["403", "Forbidden", "PermissionDenied", "AccessDenied"]):
            return jsonify({"error": error_msg}), 403
        return jsonify({"error": error_msg}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
