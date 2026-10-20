"""Atomic checkpoints with bounded retry for Windows file-sharing conflicts."""
import os
import time


def replace_checkpoint(source, target):
    for attempt in range(6):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.1 * 2 ** attempt)
