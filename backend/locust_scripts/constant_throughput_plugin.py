import time
import os
from locust import HttpUser, task, User
from locust.runners import WorkerRunner
import gevent

# --- Configuration from Environment Variables ---
TARGET_QPS_STR = os.getenv("TARGET_QPS", "10") # Target Queries Per Second per user, or total if only one user.
try:
    TARGET_QPS = int(TARGET_QPS_STR)
    if TARGET_QPS <= 0:
        print("Warning: TARGET_QPS must be positive. Defaulting to 10.")
        TARGET_QPS = 10
except ValueError:
    print(f"Warning: Invalid TARGET_QPS value '{TARGET_QPS_STR}'. Defaulting to 10.")
    TARGET_QPS = 10

# --- Request details (also from environment, similar to generic_test.py) ---
# These are needed here because the plugin might directly make requests
TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8080/default_path")
REQUEST_METHOD = os.getenv("REQUEST_METHOD", "GET")
HEADERS_STR = os.getenv("HEADERS", "{}")
PAYLOAD_STR = os.getenv("PAYLOAD", "{}")

try:
    import json
    HEADERS = json.loads(HEADERS_STR)
    PAYLOAD = json.loads(PAYLOAD_STR) if PAYLOAD_STR else None
except json.JSONDecodeError as e:
    print(f"Error parsing JSON for plugin: {e}")
    HEADERS = {}
    PAYLOAD = None


class ConstantQPSUserMixin(User): # Inherit from User, not HttpUser, to be more generic if needed
    """
    Mixin class to be used with a Locust User class to achieve a target QPS.
    Each user running with this mixin will attempt to send TARGET_QPS requests per second.
    Note: If used with HttpUser, self.client will be available.
          If used with a plain User, request logic needs to be handled differently.
    """
    abstract = True # Ensures Locust doesn't try to run this directly
    wait_time = None # Disable Locust's default wait_time for users with this mixin

    def __init__(self, environment):
        super().__init__(environment)
        self.target_qps = TARGET_QPS
        self._timer_greenlet = None
        self._tasks_to_execute = [] # Store tasks defined in the concrete class

    def on_start(self):
        """
        When the user starts, also start the QPS timer loop.
        """
        if not self.target_qps > 0:
            print(f"User {self._user_id()}: Target QPS is not positive ({self.target_qps}), QPS timer will not start.")
            return

        print(f"User {self._user_id()} starting with target QPS: {self.target_qps}. Interval: {1.0 / self.target_qps:.4f}s")

        # Collect tasks defined in the subclass that are decorated with @task
        # The tasks in the mixin should not be the ones scheduled by this timer directly.
        # Instead, this mixin should provide a method that the subclass's tasks can call
        # while respecting the QPS. OR, this mixin's timer calls a specific method.

        # For this approach, we assume the subclass will have tasks like `make_request_at_qps`
        # which internally call `self.execute_request_under_qps_control`.
        # A simpler model: this timer directly invokes a specific task from the subclass.
        # Let's find tasks decorated with @task in the concrete class.
        # self._tasks_to_execute = [t for t in self.tasks if hasattr(t, "is_locust_task") and t.is_locust_task]
        # This is complex. A simpler way: the mixin defines a single task that is rate-limited.
        # The concrete class's tasks will be scheduled by Locust as usual, but if this mixin is active,
        # it might override or supplement that.

        # Let's redefine: this mixin will provide a single task that, when called by Locust's scheduler,
        # will be rate-limited by its internal QPS logic. This is more aligned with how HttpUser tasks work.
        # The `@task` decorator in the concrete class will still determine how often Locust tries to run it,
        # but this class's `wait_time` (if implemented in the timer) will enforce the QPS.

        # Alternative: A dedicated greenlet that fires tasks.
        # This is more robust for precise QPS.
        if self.environment.runner and not isinstance(self.environment.runner, WorkerRunner):
             # Only MasterRunner and LocalRunner should spawn this, not workers if the master is doing it.
             # However, for QPS per user, each user (even on workers) needs its own timer.
             pass

        self._timer_greenlet = self.environment.greenlet_spawn(self._qps_timer_loop)
        if hasattr(super(), 'on_start'):
            super().on_start()

    def _user_id(self):
        # Helper to get a user identifier, works for local and distributed runs
        if self.environment.runner:
            return self.environment.runner.client_id # Worker ID or "local"
        return "unknown_user"

    def _qps_timer_loop(self):
        if not self.target_qps > 0:
            return

        interval = 1.0 / self.target_qps
        print(f"User {self._user_id()}: QPS timer started. Interval: {interval:.4f}s, QPS: {self.target_qps}")

        while True:
            start_time = time.time()
            try:
                # Execute the primary action defined in the user class
                # This requires the concrete class to define a method like `execute_main_task`
                if hasattr(self, 'execute_main_task_for_qps'):
                    self.execute_main_task_for_qps()
                else:
                    # Fallback or error: the concrete class needs to provide the task
                    # For now, let's assume HttpUser methods if self.client exists
                    if hasattr(self, 'client'):
                        self._perform_http_request() # A generic request method
                    else:
                        print(f"User {self._user_id()}: No 'execute_main_task_for_qps' method found and not an HttpUser.")
                        # If no task, we still need to sleep to avoid a tight loop
                        gevent.sleep(interval) # prevent busy loop if no task defined
                        continue

            except Exception as e:
                if self.environment and self.environment.events:
                    self.environment.events.request.fire(
                        request_type="QPS_TASK_EXECUTION_ERROR",
                        name="QPSInternalError",
                        response_time=0,
                        response_length=0,
                        exception=e,
                        context=self.context(),
                    )
                print(f"User {self._user_id()}: Error in QPS timer loop: {e}")
                # Potentially stop the timer or user on repeated errors

            # Calculate time spent and sleep for the remainder of the interval
            time_spent = time.time() - start_time
            sleep_time = interval - time_spent
            if sleep_time > 0:
                gevent.sleep(sleep_time)
            # else: if time_spent > interval, we are lagging behind the target QPS.
            # Optionally, log this occurrence.
            # print(f"User {self._user_id()}: Loop took {time_spent:.4f}s, sleeping for {sleep_time:.4f}s")


    def _perform_http_request(self):
        """
        Default HTTP request logic if the user is an HttpUser and
        execute_main_task_for_qps is not defined.
        This is called by the _qps_timer_loop.
        """
        if not hasattr(self, 'client'):
            print(f"User {self._user_id()}: _perform_http_request called but 'client' (HttpUser client) is not available.")
            return

        # Use environment variables for request details, similar to locust_generic_test.py
        # This makes the plugin usable standalone or with GenericUser if configured for QPS mode.
        request_name = f"{REQUEST_METHOD}_{TARGET_URL}_QPS"
        try:
            if REQUEST_METHOD.upper() == "GET":
                self.client.get(TARGET_URL, headers=HEADERS, name=request_name)
            elif REQUEST_METHOD.upper() == "POST":
                self.client.post(TARGET_URL, headers=HEADERS, json=PAYLOAD, name=request_name)
            # Add other methods as needed
            else:
                print(f"User {self._user_id()}: Unsupported HTTP method for QPS plugin: {REQUEST_METHOD}")
                # Fire a failure event for unsupported methods if desired
                if self.environment and self.environment.events:
                    self.environment.events.request.fire(
                        request_type=REQUEST_METHOD,
                        name=request_name,
                        response_time=0,
                        response_length=0,
                        exception=NotImplementedError(f"Unsupported method: {REQUEST_METHOD}"),
                        context=self.context(),
                    )
        except Exception as e:
            # Log and fire event for any exception during the request
            print(f"User {self._user_id()}: Exception during QPS HTTP request to {TARGET_URL}: {e}")
            if self.environment and self.environment.events:
                self.environment.events.request.fire(
                    request_type=REQUEST_METHOD,
                    name=request_name,
                    response_time=0, # Or measure time until exception
                    response_length=0,
                    exception=e,
                    context=self.context(),
                )


    def on_stop(self):
        """
        When the user stops, kill the timer greenlet.
        """
        print(f"User {self._user_id()} stopping QPS timer.")
        if self._timer_greenlet:
            self._timer_greenlet.kill(block=False) # Non-blocking kill
        if hasattr(super(), 'on_stop'):
            super().on_stop()

# Example of how this mixin would be used in locust_generic_test.py:
#
# from .constant_throughput_plugin import ConstantQPSUserMixin, TARGET_QPS
#
# class MyQPSUser(ConstantQPSUserMixin, HttpUser):
#     host = "http://example.com" # Set host if not done globally
#
#     # This method will be called by the QPS timer loop in ConstantQPSUserMixin
#     def execute_main_task_for_qps(self):
#         # print(f"User {self._user_id()} executing main task via QPS. Target: {TARGET_QPS} QPS")
#         # self.client.get("/some_endpoint")
#         # Or use the generic _perform_http_request from the mixin if TARGET_URL etc. are set
#         self._perform_http_request()
#
#     # We might not even need a @task here if the QPS timer drives all actions.
#     # If we do have @task, Locust will also try to schedule them based on its own logic,
#     # which might conflict or duplicate if not handled carefully.
#     # For a pure QPS model driven by the plugin, tasks here might be empty or not present.
#
#     # @task
#     # def dummy_task_for_locust_scheduler(self):
#     #     # This task would be scheduled by Locust's default mechanism.
#     #     # If the QPS timer is also running, care must be taken.
#     #     # print(f"User {self._user_id()} dummy_task_for_locust_scheduler called by Locust")
#     #     pass
#
#     def on_start(self):
#         super().on_start() # This will call ConstantQPSUserMixin.on_start
#         print(f"MyQPSUser instance started. User ID if available: {self._user_id()}")
#
#     def on_stop(self):
#         super().on_stop() # This will call ConstantQPSUserMixin.on_stop
#         print(f"MyQPSUser instance stopped. User ID if available: {self._user_id()}")

if __name__ == "__main__":
    print("This is a Locust plugin for constant QPS.")
    print(f"It's intended to be imported as a mixin, e.g., ConstantQPSUserMixin, TARGET_QPS: {TARGET_QPS}.")
    print("To test this plugin directly (for development):")
    print("1. Ensure locust is installed.")
    print("2. Create a simple locustfile that uses this mixin, like:")
    print("""
from locust import HttpUser, run_single_user
from constant_throughput_plugin import ConstantQPSUserMixin, TARGET_QPS

class TestQPSUser(ConstantQPSUserMixin, HttpUser):
    host = "https://www.google.com" # Replace with your test target

    # This method is called by the QPS timer
    def execute_main_task_for_qps(self):
        print(f"User executing request to {self.host} at {TARGET_QPS} QPS")
        self.client.get("/") # Replace with your desired request

    def on_start(self):
        print("TestQPSUser starting...")
        super().on_start() # Important to call mixin's on_start

# To run this test user:
# if __name__ == '__main__':
#     run_single_user(TestQPSUser)
    """)
    # Note: run_single_user is good for quick local tests of a User class.
    # For full tests, use `locust -f your_locust_file.py`
    pass
