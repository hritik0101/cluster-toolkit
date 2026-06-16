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

import re
import logging

logger = logging.getLogger("verifier")

# A list of regular expressions that represent destructive or modifying commands.
# This approach allows standard diagnostic commands (even with sudo) while catching mutative actions.
DENYLIST_PATTERNS = [
    # --- GCP Mutative Commands ---
    r"gcloud\s+compute\s+instances\s+(delete|stop|reset)",
    r"gcloud\s+container\s+clusters\s+(delete|update|upgrade)",
    r"gcloud\s+projects\s+delete",

    # --- Networking Modifications ---
    r"iptables\s+-[ADIFZXP]",  # Append, Delete, Insert, Flush, Zero, Policy, Purge
    r"firewall-cmd\s+--(add|remove|delete|update)",
    r"ip\s+(route|link)\s+(add|del|delete|change|replace|set)",

    # --- Destructive File Operations ---
    # Matches aggressive removes on system dirs, e.g., rm -rf /etc, rm -f *, but allows rm on temp logs if needed
    r"rm\s+-r?[f]?[r]?\s+(/|\*|/etc/?.*|/var/?.*|/usr/?.*|/bin/?.*|~/?.*)",
    # Prevent wiping disks with random/zero data
    r"dd\s+if=/dev/(zero|random|urandom)\s+of=",
    # Prevent shell redirection from overwriting critical system files
    r">\s*/(etc|var|usr|bin)/",

    # --- Disk Formatting ---
    r"mkfs(\..*)?\s+",
    r"fdisk\s+",
    r"parted\s+",

    # --- Unverified External Scripts Piped to Shell ---
    r"(curl|wget)\s+.*\|\s*(bash|sh|zsh)",

    # --- Process Killing ---
    # The agent should diagnose processes, not kill them
    r"kill\s+-9",
    r"killall\s+",
    r"pkill\s+",

    # --- System Reboot/Shutdown ---
    r"\b(reboot|shutdown|poweroff|halt)\b",
    r"init\s+[06]",
    r"systemctl\s+(reboot|poweroff|halt)",

    # --- Service State Modifications ---
    r"systemctl\s+(stop|disable|restart|mask)",
    r"service\s+\S+\s+(stop|restart|disable)",

    # --- Kubernetes/Helm Modifications ---
    r"kubectl\s+(apply|create|delete|edit|patch|scale|replace|cordon|drain|uncordon|auth|label|annotate)",
    r"helm\s+(install|upgrade|delete|uninstall|rollback)",

    # --- Package Management ---
    # Prevent on-the-fly installations or removals
    r"(apt|apt-get|yum|dnf|rpm|dpkg|apk|snap)\s+(install|remove|purge|upgrade|autoremove)"
]

def is_command_safe(command: str) -> bool:
    """
    Checks if a command is safe to execute by matching against the denylist.
    Returns True if safe, False if the command contains a forbidden pattern.
    """
    for pattern in DENYLIST_PATTERNS:
        if re.search(pattern, command):
            logger.warning(f"Command rejected by denylist pattern '{pattern}': {command}")
            return False
            
    return True
