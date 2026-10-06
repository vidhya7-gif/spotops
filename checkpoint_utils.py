import json
import hashlib
import os

CHECKPOINT_FILE = "checkpoint.json"

def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def save_checkpoint(epoch, accuracy):
    data = {"epoch": epoch, "accuracy": accuracy}
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f)
    checksum = _file_hash(CHECKPOINT_FILE)
    with open(CHECKPOINT_FILE + ".md5", "w") as f:
        f.write(checksum)
    print(f"[checkpoint] saved epoch {epoch}, accuracy {accuracy:.4f}")
    push_checkpoint_to_storage()

def load_checkpoint():
    pull_checkpoint_from_storage()
    if not os.path.exists(CHECKPOINT_FILE) or not os.path.exists(CHECKPOINT_FILE + ".md5"):
        return None
    with open(CHECKPOINT_FILE + ".md5") as f:
        saved_checksum = f.read().strip()
    actual_checksum = _file_hash(CHECKPOINT_FILE)
    if saved_checksum != actual_checksum:
        print("[checkpoint] WARNING: checkpoint file corrupted, ignoring it")
        return None
    with open(CHECKPOINT_FILE) as f:
        data = json.load(f)
    print(f"[checkpoint] verified OK, resuming from epoch {data['epoch']}")
    return data

import subprocess

STORAGE_VM_IP = "3.27.11.65"
SSH_KEY_PATH = os.path.expanduser("~/spotops-aws-key-v2.pem")
def push_checkpoint_to_storage():
    subprocess.run([
        "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        CHECKPOINT_FILE, CHECKPOINT_FILE + ".md5",
        f"ubuntu@{STORAGE_VM_IP}:/home/ubuntu/"
    ])

def pull_checkpoint_from_storage():
    subprocess.run([
        "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{STORAGE_VM_IP}:/home/ubuntu/{CHECKPOINT_FILE}",
        "."
    ])
    subprocess.run([
        "scp", "-i", SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{STORAGE_VM_IP}:/home/ubuntu/{CHECKPOINT_FILE}.md5",
        "."
    ])
