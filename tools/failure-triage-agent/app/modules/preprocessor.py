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
import re
import logging
import difflib

logger = logging.getLogger("preprocessor")

def strip_ansi(line: str) -> str:
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', line)

def compress_spaces_and_stars(line: str) -> str:
    line = re.sub(r'\*{2,}', '*', line)
    line = re.sub(r' {2,}', ' ', line)
    return line

def is_hard_noise(line: str) -> bool:
    stripped = line.strip()
    
    # Docker Noise (Strictly anchored to avoid deleting "Waiting for database...")
    if re.match(r'^([0-9a-f]{12}: )?(Pulling fs layer|Waiting|Verifying Checksum|Download complete|Pull complete)$', stripped):
        return True
    if re.match(r'^(Digest: sha256:|Status: Downloaded newer image|Already have image \(with digest\)|Pulling from )', stripped):
        return True
    if re.match(r'^[0-9a-f]{12}: (Pulling|Waiting|Verifying|Download|Pull)', stripped):
        return True
    
    # Apt/Dpkg Noise (Removed "Setting up" to preserve context before package failure)
    if re.match(r'^(Get|Hit|Ign):\d+\s+http', stripped):
        return True
    if re.match(r'^(Fetched\s+\d+\s+[kM]B|Reading package lists|Building dependency tree|Reading state information|Unpacking\s+|Selecting previously unselected|Preparing to unpack|Processing triggers for)', stripped):
        return True
        
    # Cloud Build Boilerplate
    if re.match(r'^(FETCHSOURCE|SETUPBUILD|BUILD|Starting Step|Finished Step)', stripped):
        return True
        
    return False

def mask_noise(line: str) -> str:
    line = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<UUID>', line, flags=re.IGNORECASE)
    line = re.sub(r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?', '<TIME>', line)
    line = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<IP>', line)
    line = re.sub(r'\b\d+\b', '<NUM>', line)
    return line

def deduplicate_block(lines: list) -> list:
    deduped = []
    prev_abstract = None
    dup_count = 0
    last_real_line = None
    
    for line in lines:
        abstract = mask_noise(line)
        if abstract == prev_abstract:
            dup_count += 1
        else:
            if dup_count > 0 and last_real_line is not None:
                deduped.append(f"[... previous line pattern repeated {dup_count} times ...]")
            deduped.append(line)
            last_real_line = line
            dup_count = 0
            prev_abstract = abstract
            
    if dup_count > 0 and last_real_line is not None:
        deduped.append(f"[... previous line pattern repeated {dup_count} times ...]")
        
    return deduped

class TaskBlock:
    def __init__(self, header=""):
        self.header = header
        self.lines = []
        self.is_important = False
        
    def add_line(self, line):
        self.lines.append(line)
        
    def evaluate_importance(self):
        error_keywords = re.compile(r'(error|failed|fatal|exception|traceback|timeout|denied|killed|terminated|refused|exit code|not found|unreachable)', re.IGNORECASE)
        payload_keywords = re.compile(r'(MSG:|=> \{)')
        
        for line in self.lines:
            if error_keywords.search(line) or payload_keywords.search(line):
                self.is_important = True
                return

def truncate_payload(lines: list) -> list:
    """If a payload is massive, we truncate the middle but keep the ends and any internal errors."""
    if len(lines) <= 50:
        return lines
        
    error_keywords = re.compile(r'(error|failed|fatal|exception|traceback|timeout|denied|killed|terminated|refused|exit code|not found|unreachable)', re.IGNORECASE)
    
    first_chunk = lines[:15]
    last_chunk = lines[-35:]
    
    middle = lines[15:-35]
    middle_filtered = []
    
    gap_count = 0
    for line in middle:
        if error_keywords.search(line):
            if gap_count > 0:
                middle_filtered.append(f"\n[... {gap_count} lines of normal execution truncated ...]\n")
                gap_count = 0
            middle_filtered.append(line)
        else:
            gap_count += 1
            
    if gap_count > 0:
        middle_filtered.append(f"\n[... {gap_count} lines of normal execution truncated ...]\n")
        
    return first_chunk + middle_filtered + last_chunk

def filter_with_sliding_window(lines: list[str], window: int = 15) -> list[str]:
    if len(lines) <= 50:
        return lines
        
    error_regex = re.compile(r'(?i)(error|fail|warn|fatal|critical|denied|oom|traceback|panic|timeout|exception|killed|terminated|refused|exit code|not found|unreachable)')
    
    keep_indices = set()
    for i in range(min(10, len(lines))):
        keep_indices.add(i)
    for i in range(max(0, len(lines)-10), len(lines)):
        keep_indices.add(i)
        
    for i, line in enumerate(lines):
        if error_regex.search(line):
            for j in range(max(0, i - window), min(len(lines), i + window + 1)):
                keep_indices.add(j)
                
    if len(keep_indices) == len(lines):
        return lines
        
    filtered = []
    last_idx = -1
    for idx in sorted(keep_indices):
        if last_idx != -1 and idx > last_idx + 1:
            gap = idx - last_idx - 1
            filtered.append(f"\n[... {gap} lines of normal execution truncated ...]\n")
        filtered.append(lines[idx])
        last_idx = idx
        
    return filtered

def fuzzy_deduplicate(lines: list[str], threshold: float = 0.85) -> list[str]:
    deduped = []
    prev_abstract = None
    dup_count = 0
    last_real_line = None
    
    for line in lines:
        if line.startswith('\n[... ') or line.startswith('[... '):
            if dup_count > 0 and last_real_line is not None:
                deduped.append(f"[... previous line repeated {dup_count} times (similar) ...]")
                dup_count = 0
            deduped.append(line)
            prev_abstract = None
            last_real_line = None
            continue
            
        abstract = mask_noise(line)
        
        if prev_abstract is not None:
            similarity = difflib.SequenceMatcher(None, abstract, prev_abstract).ratio()
        else:
            similarity = 0.0
            
        if similarity >= threshold:
            dup_count += 1
        else:
            if dup_count > 0 and last_real_line is not None:
                deduped.append(f"[... previous line repeated {dup_count} times (similar) ...]")
            deduped.append(line)
            last_real_line = line
            dup_count = 0
            prev_abstract = abstract
            
    if dup_count > 0 and last_real_line is not None:
        deduped.append(f"[... previous line repeated {dup_count} times (similar) ...]")
        
    return deduped

def enforce_strict_limits(lines: list[str], max_lines: int = 300) -> list[str]:
    if len(lines) <= max_lines:
        return lines
        
    head_size = max_lines // 3
    tail_size = max_lines - head_size
    
    first_chunk = lines[:head_size]
    last_chunk = lines[-tail_size:]
    gap = len(lines) - max_lines
    
    return first_chunk + [f"\n[... STRICT LIMIT: {gap} lines dropped from middle ...]\n"] + last_chunk


def process_in_memory(content: str, source_type: str) -> str:
    """
    Processes logs or command outputs in memory.
    source_type can be: 'intake_log', 'download', or 'command'
    """
    # Remove gcloud IAP NumPy warnings
    iap_warning = "WARNING: \n\nTo increase the performance of the tunnel, consider installing NumPy. For instructions,\nplease see https://cloud.google.com/iap/docs/using-tcp-forwarding#increasing_the_tcp_upload_bandwidth\n"
    content = content.replace(iap_warning, "")
    
    lines = content.splitlines()
    
    if source_type == 'intake_log':
        prefix_regex = re.compile(r'^Step #\d+ - "[^"]+":\s*')
        task_header_regex = re.compile(r'^(TASK|PLAY|RUNNING HANDLER) \[.*?\]')
        
        blocks = []
        current_block = TaskBlock(header="[Pre-Execution / System Setup]")
        blocks.append(current_block)
        
        for line in lines:
            clean_line = prefix_regex.sub('', line).rstrip()
            clean_line = strip_ansi(clean_line)
            clean_line = compress_spaces_and_stars(clean_line)
            
            if not clean_line.strip() or is_hard_noise(clean_line):
                continue
                
            if task_header_regex.search(clean_line):
                current_block = TaskBlock(header=clean_line)
                blocks.append(current_block)
            else:
                current_block.add_line(clean_line)
                
        if blocks:
            blocks[-1].is_important = True
            
        final_output = []
        for block in blocks:
            block.evaluate_importance()
            
            if block.header == "[Pre-Execution / System Setup]" and not block.is_important:
                continue
                
            final_output.append(block.header)
            
            if block.is_important:
                deduped_lines = deduplicate_block(block.lines)
                truncated_lines = truncate_payload(deduped_lines)
                final_output.extend(truncated_lines)
                
        return '\n'.join(final_output)

    elif source_type == 'download':
        cleaned_content = []
        for line in lines:
            stripped_line = line.strip()
            if not stripped_line:
                continue
            # Remove full-line comments (YAML, Terraform, bash, etc.)
            if stripped_line.startswith('#') or stripped_line.startswith('//'):
                continue
            cleaned_content.append(line.rstrip())
        return '\n'.join(cleaned_content)

    elif source_type == 'command':
        if not lines:
            return "[No output]"
            
        compressed_lines = [compress_spaces_and_stars(line) for line in lines]
        filtered_lines = filter_with_sliding_window(compressed_lines, window=15)
        deduped_lines = fuzzy_deduplicate(filtered_lines, threshold=0.85)
        limited_lines = enforce_strict_limits(deduped_lines, max_lines=300)
        return '\n'.join(limited_lines)
        
    return ""
