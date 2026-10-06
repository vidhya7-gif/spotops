import json
import hashlib
import os
import subprocess
import time

CHECKPOINT_FILE = "checkpoint.json"
STORAGE_VM_IP = "3.27.11.65"
SSH_KEY_PATH = os.path.expanduser("~/spotops-aws-key-v2.pem")
ROLE_FILE = os.path.expanduser("~/vm_role")


def get_vm_role():
    try:
        with open(ROLE_FILE) as f:
            return f.read().strip() or "unknown"
    except OSError:
        return "laptop"


def _file_hash(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def _read_history():
    try:
        with open(CHECKPOINT_FILE) as f:
            return json.load(f).get("history", [])
    except (OSError, ValueError):
        return []


def save_checkpoint(epoch, accuracy):
    vm = get_vm_role()
    now = time.time()
    history = [h for h in _read_history() if h["epoch"] < epoch]
    history.append({"epoch": epoch, "accuracy": round(accuracy, 4), "vm": vm, "time": now})
    data = {"epoch": epoch, "accuracy": accuracy, "vm": vm, "time": now, "history": history}
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f)
    checksum = _file_hash(CHECKPOINT_FILE)
    with open(CHECKPOINT_FILE + ".md5", "w") as f:
        f.write(checksum)
    print(f"[checkpoint] saved epoch {epoch}, accuracy {accuracy:.4f} (vm: {vm})")
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