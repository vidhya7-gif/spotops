import time
import random
from checkpoint_utils import save_checkpoint, load_checkpoint

TOTAL_EPOCHS = 20

def main():
    checkpoint = load_checkpoint()
    start_epoch = checkpoint["epoch"] + 1 if checkpoint else 1
    accuracy = checkpoint["accuracy"] if checkpoint else 0.0

    if start_epoch > 1:
        print(f"Resuming training from epoch {start_epoch}...")
    else:
        print("Starting training from scratch...")

    for epoch in range(start_epoch, TOTAL_EPOCHS + 1):
        print(f"Epoch {epoch}/{TOTAL_EPOCHS} running...")
        time.sleep(5)
        accuracy = min(accuracy + random.uniform(0.01, 0.05), 0.99)
        save_checkpoint(epoch, accuracy)

    print("Training complete.")

if __name__ == "__main__":
    main()
