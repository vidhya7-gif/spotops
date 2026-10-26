"""Checkpoint + AWS helpers for the real-dataset SpotOps demo.

Every checkpoint is replicated three ways:
  1. the storage VM (scp)   - what the dashboard reads
  2. Amazon S3              - durable copy, restored if the storage VM is lost
  3. CloudWatch metrics     - Epoch / Accuracy time series + alarms
S3 and CloudWatch use the VM's IAM role (no keys on disk). If the role or
boto3 is missing they switch themselves off and training carries on.
"""
import json
import os
import subprocess
import time

import joblib

HOME = os.environ.get("SPOTOPS_HOME", os.path.expanduser("~"))
CKPT = "checkpoint_real.json"
MODEL = "model_real.pkl"
DATA = "dataset.csv"
DATA_NAME = "dataset.name"
NOTICE = "spot_notice.json"
STORAGE_IP = "3.27.11.65"
FALLBACK_IP = "13.210.203.78"
KEY_PATH = os.path.join(HOME, "spotops-aws-key-v2.pem")
REGION = "ap-southeast-2"
_SSH = ["-i", KEY_PATH, "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5"]
_AWS = {"bucket": None, "tried": False}


def _run(cmd, t=25):
    try:
        return subprocess.run(cmd, capture_output=True, timeout=t).returncode == 0
    except Exception:
        return False


def _scp_up(files):
    return _run(["scp"] + _SSH + list(files) + ["ubuntu@%s:/home/ubuntu/" % STORAGE_IP])


def _scp_down(name):
    return _run(["scp"] + _SSH + ["ubuntu@%s:/home/ubuntu/%s" % (STORAGE_IP, name), "."])


def start_fallback():
    return _run(["ssh"] + _SSH + ["ubuntu@%s" % FALLBACK_IP,
                 "nohup python3 -u train_real.py fallback > train_real.log 2>&1 &"], 15)


# ---------------------------------------------------------------- AWS (IAM role)
def _bucket():
    if not _AWS["tried"]:
        _AWS["tried"] = True
        try:
            import boto3
            acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
            _AWS["bucket"] = "spotops-ckpt-" + acct
        except Exception as e:
            print("[aws] no IAM role/boto3 on this VM -> S3 + CloudWatch off (%s)" % type(e).__name__)
    return _AWS["bucket"]


def _client(name):
    import boto3
    return boto3.client(name, region_name=REGION)


def s3_push():
    b = _bucket()
    if not b:
        return "off"
    try:
        c = _client("s3")
        for f in (CKPT, MODEL):
            c.upload_file(f, b, "checkpoints/" + f)
        return "ok"
    except Exception as e:
        print("[s3] upload failed: %s" % e)
        return "error"


def s3_pull():
    b = _bucket()
    if not b:
        return False
    try:
        c = _client("s3")
        for f in (CKPT, MODEL):
            c.download_file(b, "checkpoints/" + f, f)
        return True
    except Exception:
        return False


def s3_clear():
    b = _bucket()
    if not b:
        return
    try:
        c = _client("s3")
        for f in (CKPT, MODEL):
            c.delete_object(Bucket=b, Key="checkpoints/" + f)
    except Exception as e:
        print("[s3] clear failed: %s" % e)


def cw_put(epoch, acc):
    if not _bucket():
        return
    try:
        _client("cloudwatch").put_metric_data(Namespace="SpotOps", MetricData=[
            {"MetricName": "Epoch", "Value": float(epoch), "Unit": "Count"},
            {"MetricName": "Accuracy", "Value": float(acc) * 100, "Unit": "Percent"}])
    except Exception as e:
        print("[cloudwatch] put failed: %s" % e)


def cw_notice():
    if not _bucket():
        return
    try:
        _client("cloudwatch").put_metric_data(Namespace="SpotOps", MetricData=[
            {"MetricName": "SpotInterruptionNotice", "Value": 1.0, "Unit": "Count"}])
    except Exception as e:
        print("[cloudwatch] notice put failed: %s" % e)


# ---------------------------------------------------------------- checkpoints
def _write_json(epoch, acc, vm, history, dataset, s3):
    with open(CKPT, "w") as f:
        json.dump({"epoch": epoch, "accuracy": acc, "vm": vm, "time": time.time(),
                   "history": history, "dataset": dataset, "s3": s3}, f)


def save_checkpoint(epoch, acc, vm, history, state, dataset):
    joblib.dump(state, MODEL)
    _write_json(epoch, acc, vm, history, dataset, "pending")
    s3 = s3_push()
    _write_json(epoch, acc, vm, history, dataset, s3)
    ok = _scp_up([CKPT, MODEL])
    cw_put(epoch, acc)
    print("[checkpoint] epoch %d acc %.4f | storage VM:%s S3:%s" % (epoch, acc, "ok" if ok else "FAILED", s3))


def load_checkpoint():
    _scp_down(CKPT)
    _scp_down(MODEL)
    if not (os.path.exists(CKPT) and os.path.exists(MODEL)):
        if s3_pull():
            print("[checkpoint] storage VM had nothing -> restored from Amazon S3")
    if not (os.path.exists(CKPT) and os.path.exists(MODEL)):
        return None, None
    try:
        with open(CKPT) as f:
            data = json.load(f)
        state = joblib.load(MODEL)
        print("[checkpoint] resuming from epoch %d (model weights restored)" % data["epoch"])
        return data, state
    except Exception as e:
        print("[checkpoint] could not read checkpoint (%s) -> starting fresh" % e)
        return None, None


def pull_dataset():
    """Fetch the dashboard-uploaded dataset, if any. Returns its file name or None."""
    if _scp_down(DATA_NAME) and _scp_down(DATA):
        try:
            return open(DATA_NAME).read().strip() or "uploaded.csv"
        except OSError:
            pass
    for f in (DATA, DATA_NAME):
        try:
            os.remove(f)
        except OSError:
            pass
    return None


def push_notice(vm):
    with open(NOTICE, "w") as f:
        json.dump({"time": time.time(), "vm": vm}, f)
    _scp_up([NOTICE])
