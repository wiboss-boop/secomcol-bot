#!/usr/bin/env python3
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Wait a few seconds so any previous instance can release the Telegram polling lock
time.sleep(5)
os.execlp("python", "python", "src/bot.py")
