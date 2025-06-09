import time
import gevent
import logging
from locust.env import Environment

logger = logging.getLogger(__name__)

class ConstantThroughput:
    """
    A custom wait_time callable that makes sure a single user executes tasks
    at a maximum calculated rate per second, dynamically adapting to the total user count.

    Args:
        target_qps (float): The *global* target requests per second for the *entire* test.
                            This QPS will be distributed among all active users.
        env (locust.env.Environment): The Locust Environment object, used to get
                                      the current total user count dynamically.
    """
    def __init__(self, target_qps: float, env: Environment):
        if not isinstance(target_qps, (int, float)):
            raise TypeError("target_qps must be a number.")
        if target_qps < 0:
            logger.warning(f"ConstantThroughput initialized with negative target_qps ({target_qps}). Treating as 0 (no wait).")

        self.target_qps = target_qps
        self.env = env
        self._last_task_start_time = time.monotonic()
        logger.info(f"Initialized ConstantThroughput with global target QPS: {self.target_qps}.")

    def __call__(self):
        """
        Calculates the per-user rate based on the current total user count,
        then waits if necessary to maintain that rate.
        """
        # --- Robustness Check: Ensure runner is active and has users ---
        # If the runner is not yet active or there are no users, we can't calculate throughput.
        # In such cases, return 0 to allow tasks to proceed without waiting,
        # or implement a minimal default wait if that's desired.
        if not self.env or not self.env.runner or self.env.runner.user_count <= 0:
            self._last_task_start_time = time.monotonic()
            return 0  # No wait if runner not ready or no users

        current_user_count = self.env.runner.user_count

        # Handle edge cases for target_qps (already checked in init, but safe to double-check)
        if self.target_qps <= 0:
            self._last_task_start_time = time.monotonic()
            return 0

        # Calculate the desired per-user rate based on current user count
        current_per_user_rate = self.target_qps / current_user_count

        # If the calculated per-user rate is too low or invalid, treat as no wait
        if current_per_user_rate <= 0:
            self._last_task_start_time = time.monotonic()
            return 0

        current_interval = 1.0 / current_per_user_rate

        current_time = time.monotonic()
        elapsed_since_last_task = current_time - self._last_task_start_time

        time_to_wait = current_interval - elapsed_since_last_task
        actual_wait_duration = 0

        if time_to_wait > 0:
            actual_wait_duration = time_to_wait
            gevent.sleep(time_to_wait)

        self._last_task_start_time = time.monotonic()
        return actual_wait_duration