#!/bin/bash
SPOT_IP="16.176.137.75"
STORAGE_IP="3.27.11.65"
KEY="$HOME/spotops-aws-key-v2.pem"
TOTAL_EPOCHS=20
SSH="ssh -i $KEY -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes"

seen_running=0
misses=0
echo "$(date) monitor started"

while true; do
  if $SSH ubuntu@$SPOT_IP "pgrep -f '[t]rain.py'" > /dev/null 2>&1; then
    seen_running=1
    misses=0
    echo "$(date) OK: spot VM is training"
  elif [ $seen_running -eq 1 ]; then
    misses=$((misses+1))
    echo "$(date) WARN: spot VM not training (miss $misses of 2)"
    if [ $misses -ge 2 ]; then
      EPOCH=$($SSH ubuntu@$STORAGE_IP "cat checkpoint.json" 2>/dev/null | grep -o '"epoch": [0-9]*' | head -n1 | grep -o '[0-9]*')
      if [ "${EPOCH:-0}" -ge $TOTAL_EPOCHS ]; then
        echo "$(date) DONE: training finished normally at epoch $EPOCH"
        exit 0
      fi
      echo "$(date) FAILOVER: spot VM lost at epoch ${EPOCH:-0}, resuming on fallback VM"
      cd /home/ubuntu
      nohup python3 -u train.py > failover.log 2>&1 &
      echo "$(date) failover started, see failover.log"
      exit 0
    fi
  else
    echo "$(date) waiting for training to start on spot VM"
  fi
  sleep 5
done