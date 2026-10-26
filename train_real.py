"""
SpotOps real training job.

Dataset: Zalando's Fashion-MNIST (clothing images, 10 categories) by default,
or any CSV you upload from the dashboard (last column = label).
Run:  python3 train_real.py spot [--fresh]    |    python3 train_real.py fallback

Spot behaviour: a background thread watches the EC2 metadata service for the
2-minute Spot interruption notice. When it appears, the job finishes the
current epoch, checkpoints (storage VM + S3), starts the fallback VM and exits.
"""
import csv
import gzip
import os
import sys
import threading
import time
import urllib.request

import numpy as np
from sklearn.linear_model import SGDClassifier
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

import checkpoint_utils_real as cu

VM = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "spot"
FRESH = "--fresh" in sys.argv
TOTAL_EPOCHS = 20
EPOCH_SECONDS = float(os.environ.get("EPOCH_SECONDS", "5"))
FASHION_URLS = ["http://fashion-mnist.s3-website.eu-central-1.amazonaws.com/",
                "https://github.com/zalandoresearch/fashion-mnist/raw/master/data/fashion/"]
notice = threading.Event()


# ------------------------------------------------------------------ datasets
def load_fashion():
    cache = "fashion_cache.npz"
    if os.path.exists(cache):
        d = np.load(cache)
        return d["X"], d["y"], ["class %d" % i for i in range(10)]

    def get(name):
        last = None
        for base in FASHION_URLS:
            try:
                with urllib.request.urlopen(base + name, timeout=40) as r:
                    return gzip.decompress(r.read())
            except Exception as e:
                last = e
        raise last

    img = get("t10k-images-idx3-ubyte.gz")      # 10,000 images keeps a 1 GB VM safe
    lab = get("t10k-labels-idx1-ubyte.gz")
    X = np.frombuffer(img, dtype=np.uint8, offset=16).reshape(-1, 784).astype("float32") / 255.0
    y = np.frombuffer(lab, dtype=np.uint8, offset=8).astype(int)
    np.savez_compressed(cache, X=X, y=y)
    return X, y, ["class %d" % i for i in range(10)]


def load_csv(path):
    def isnum(s):
        try:
            float(s)
            return True
        except ValueError:
            return False
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if len(rows) < 20 or len(rows[0]) < 2:
        raise ValueError("CSV needs a header/data with >= 2 columns and >= 20 rows")
    if not all(isnum(c) for c in rows[0][:-1]):
        rows = rows[1:]                          # header row
    X, lab = [], []
    for r in rows[:20000]:
        if len(r) >= 2 and all(isnum(c) for c in r[:-1]):
            X.append([float(c) for c in r[:-1]])
            lab.append(r[-1].strip())
    classes = sorted(set(lab))
    if not 2 <= len(classes) <= 50:
        raise ValueError("last column must hold 2-50 distinct labels, found %d" % len(classes))
    idx = {c: i for i, c in enumerate(classes)}
    return np.array(X, dtype="float32"), np.array([idx[c] for c in lab]), classes


def load_digits_fallback():
    from sklearn.datasets import load_digits
    X, y = load_digits(return_X_y=True)
    return X.astype("float32"), y, [str(i) for i in range(10)]


def get_data(ck):
    name = ck.get("dataset") if ck else None
    uploaded = cu.pull_dataset()
    if name is None:
        name = ("upload:" + uploaded) if uploaded else "fashion-mnist"
    if name.startswith("upload:"):
        return name, load_csv(cu.DATA)
    if name.startswith("digits"):
        return name, load_digits_fallback()
    try:
        return "fashion-mnist", load_fashion()
    except Exception as e:
        print("[data] Fashion-MNIST download failed (%s) -> using built-in digits instead" % e)
        return "digits (offline fallback)", load_digits_fallback()


# ------------------------------------------------------- Spot 2-minute warning
def imds(path):
    hdr = {}
    try:
        req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
                                     headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
        hdr = {"X-aws-ec2-metadata-token": urllib.request.urlopen(req, timeout=1).read().decode()}
    except Exception:
        pass
    try:
        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/" + path, headers=hdr)
        return urllib.request.urlopen(req, timeout=1).read().decode()
    except Exception:
        return None                              # 404 = no notice (also off-EC2)


def watch_notice():
    while not notice.is_set():
        real = imds("spot/instance-action")
        fake = os.path.exists("fake_notice")     # dashboard's "simulate 2-minute warning"
        if real or fake:
            if fake:
                os.remove("fake_notice")
            print("[spot-warning] %s -> finishing this epoch, then handing over" %
                  ("AWS 2-minute interruption notice: " + real if real else "simulated notice"))
            notice.set()
            cu.push_notice(VM)
            cu.cw_notice()
            return
        time.sleep(2)


# ------------------------------------------------------------------- training
def main():
    if FRESH:
        for f in (cu.CKPT, cu.MODEL):
            if os.path.exists(f):
                os.remove(f)
        cu.s3_clear()
        ck, state = None, None
    else:
        ck, state = cu.load_checkpoint()

    name, (X, y, classes) = get_data(ck)
    try:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)
    except ValueError:
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42)

    if state is None:
        state = {"clf": SGDClassifier(loss="log_loss", random_state=42),
                 "scaler": StandardScaler().fit(Xtr)}
        history, start = [], 1
        print("Starting from scratch on the %s VM | dataset: %s | %d train / %d test rows"
              % (VM, name, len(Xtr), len(Xte)))
    else:
        history, start = ck["history"], ck["epoch"] + 1
        print("Resuming on the %s VM from epoch %d | dataset: %s" % (VM, start, name))

    Xtr_s, Xte_s = state["scaler"].transform(Xtr), state["scaler"].transform(Xte)
    if VM == "spot":
        threading.Thread(target=watch_notice, daemon=True).start()

    for epoch in range(start, TOTAL_EPOCHS + 1):
        state["clf"].partial_fit(Xtr_s, ytr, classes=np.arange(len(classes)))
        acc = float(state["clf"].score(Xte_s, yte))
        history.append({"epoch": epoch, "accuracy": acc, "vm": VM, "time": time.time()})
        cu.save_checkpoint(epoch, acc, VM, history, state, name)
        print("Epoch %d/%d | real test accuracy %.4f" % (epoch, TOTAL_EPOCHS, acc))
        if notice.is_set() and epoch < TOTAL_EPOCHS:
            print("[spot-warning] checkpoint safe on storage VM + S3. Starting the fallback VM.")
            cu.start_fallback()
            return
        time.sleep(EPOCH_SECONDS)
    print("Training complete.")


if __name__ == "__main__":
    main()
