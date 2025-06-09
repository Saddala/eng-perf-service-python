import os
import time
import json
from threading import Lock

class LocustStatsLogger:
    def __init__(self, test_id: str, base_dir: str = "test_results"):
        self.test_id = test_id
        self.log_dir = os.path.join(base_dir, test_id)
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file_path = os.path.join(self.log_dir, "flask_locust_runner_metrics.log")
        self._file = open(self.log_file_path, "a")
        self._lock = Lock()

    def log(self, data: dict):
        with self._lock:
            self._file.write(json.dumps(data) + "\n")
            self._file.flush()

    def log_event(self, event_name: str, payload: dict):
        payload["event"] = event_name
        payload["timestamp"] = time.time()
        self.log(payload)

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()

    @staticmethod
    def from_env():
        test_id = os.getenv("TEST_ID", "unknown")
        return LocustStatsLogger(test_id)

# Singleton instance (optional)
logger_instance = None

def get_logger():
    global logger_instance
    if logger_instance is None:
        logger_instance = LocustStatsLogger.from_env()
    return logger_instance
