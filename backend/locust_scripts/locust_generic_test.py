import os
from locust import HttpUser, task, between, events
from locust.runners import MasterRunner # Required for test_start/test_stop on master

# --- Configuration from Environment Variables (Placeholders) ---
# These will be read from environment variables passed by the Flask app
TARGET_URL = os.getenv("TARGET_URL", "http://localhost:8080/default_path") # Default for local testing
REQUEST_METHOD = os.getenv("REQUEST_METHOD", "GET")
HEADERS_STR = os.getenv("HEADERS", "{}") # e.g., '{"Content-Type":"application/json", "X-API-Key":"secret"}'
PAYLOAD_STR = os.getenv("PAYLOAD", "{}") # e.g., '{"key":"value"}'
THINK_TIME_MIN_STR = os.getenv("THINK_TIME_MIN", "1") # Min wait time between tasks in seconds
THINK_TIME_MAX_STR = os.getenv("THINK_TIME_MAX", "1") # Max wait time between tasks in seconds
TEST_ID = os.getenv("TEST_ID", "default_test_run") # Unique ID for the test run
LOCUST_MODE = os.getenv("LOCUST_MODE", "generic") # 'generic', 'constant_qps'
TARGET_QPS_STR = os.getenv("TARGET_QPS", "10") # Target QPS for constant_qps mode

# --- Parse Configuration ---
try:
    import json
    HEADERS = json.loads(HEADERS_STR)
    PAYLOAD = json.loads(PAYLOAD_STR) if PAYLOAD_STR else None
    THINK_TIME_MIN = float(THINK_TIME_MIN_STR)
    THINK_TIME_MAX = float(THINK_TIME_MAX_STR)
    TARGET_QPS = int(TARGET_QPS_STR)
except json.JSONDecodeError as e:
    print(f"Error parsing JSON from environment variables: {e}")
    HEADERS = {}
    PAYLOAD = None
    THINK_TIME_MIN = 1.0
    THINK_TIME_MAX = 1.0
    TARGET_QPS = 10
except ValueError as e:
    print(f"Error converting string to number from environment variables: {e}")
    THINK_TIME_MIN = 1.0
    THINK_TIME_MAX = 1.0
    TARGET_QPS = 10


# --- Optional Constant Throughput Plugin Import ---
ConstantQPSUserMixin = None
if LOCUST_MODE == "constant_qps":
    try:
        from .constant_throughput_plugin import ConstantQPSUserMixin as PluginMixin
        ConstantQPSUserMixin = PluginMixin # Assign to the name used later
        print(f"Successfully imported ConstantQPSUserMixin. TARGET_QPS set to: {TARGET_QPS}")
    except ImportError as e:
        print(f"Error importing ConstantQPSUserMixin: {e}. Running generic user without QPS control.")
        # ConstantQPSUserMixin will remain None, and GenericUser will not use it.
    except Exception as e:
        print(f"An unexpected error occurred during import of ConstantQPSUserMixin: {e}")


# Determine the base class for GenericUser
# If QPS mode and plugin loaded, GenericUser will be (PluginMixin, HttpUser)
# Otherwise, it will be just HttpUser
base_classes = (HttpUser,)
if LOCUST_MODE == "constant_qps" and ConstantQPSUserMixin:
    base_classes = (ConstantQPSUserMixin, HttpUser)
    print("GenericUser will be configured for Constant QPS mode.")
else:
    print("GenericUser will be configured for standard (non-QPS) mode.")


class GenericUser(*base_classes):
    # wait_time is used by Locust's scheduler if no QPS plugin is active or if the plugin doesn't manage all tasks.
    # If ConstantQPSUserMixin is active, its internal timer dictates task execution frequency.
    # The mixin sets its own wait_time to None.
    # For non-QPS mode, this wait_time applies.
    if not (LOCUST_MODE == "constant_qps" and ConstantQPSUserMixin):
        wait_time = between(THINK_TIME_MIN, THINK_TIME_MAX)
    else:
        # In QPS mode, the plugin handles timing. We might not need a Locust-scheduled task
        # if the plugin's timer calls execute_main_task_for_qps directly.
        # If we still have a @task, Locust might try to schedule it.
        # The plugin sets wait_time = None on the user instance if it wants full control.
        pass


    def on_start(self):
        """
        Called when a User starts.
        """
        # Call super().on_start() to ensure mixin's on_start (if any) is called.
        # This is crucial for ConstantQPSUserMixin to start its timer.
        super().on_start() # Important for mixins like ConstantQPSUserMixin

        print(f"GenericUser instance started. User ID: {self._user_id_for_logging()}")
        print(f"  TARGET_URL: {TARGET_URL}, METHOD: {REQUEST_METHOD}")
        if PAYLOAD:
            print(f"  Payload: {PAYLOAD}")

        if LOCUST_MODE == "constant_qps" and ConstantQPSUserMixin:
            print(f"  Mode: Constant QPS (Target: {TARGET_QPS} QPS per user)")
            if not hasattr(self, 'execute_main_task_for_qps'):
                print("  ERROR: ConstantQPSUserMixin is active, but 'execute_main_task_for_qps' is not defined in GenericUser!")
        else:
            print(f"  Mode: Generic (Think time: {THINK_TIME_MIN}-{THINK_TIME_MAX}s)")


    def _user_id_for_logging(self):
        if self.environment and self.environment.runner:
            return self.environment.runner.client_id
        return "local_user"

    # This method is specifically for the ConstantQPSUserMixin
    def execute_main_task_for_qps(self):
        """
        This method is called by the ConstantQPSUserMixin's timer loop
        at the specified QPS rate.
        """
        # print(f"User {self._user_id_for_logging()} executing QPS task via execute_main_task_for_qps.")
        self._perform_configured_request()

    def _perform_configured_request(self):
        """
        Helper method to make the actual HTTP request based on configuration.
        Used by both QPS mode (via execute_main_task_for_qps) and generic @task.
        """
        request_name = f"{REQUEST_METHOD}_{TARGET_URL}"
        if LOCUST_MODE == "constant_qps" and ConstantQPSUserMixin:
            request_name += "_QPS"

        if REQUEST_METHOD.upper() == "GET":
            self.client.get(TARGET_URL, headers=HEADERS, name=request_name)
        elif REQUEST_METHOD.upper() == "POST":
            # For file uploads (multipart/form-data), PAYLOAD needs special handling.
            # The current simple PAYLOAD_STR is for JSON.
            # Actual file uploads would require self.client.post with 'files' parameter.
            # This needs to be addressed when handling file uploads from Flask.
            # For now, assuming JSON payload if POST.
            self.client.post(TARGET_URL, headers=HEADERS, json=PAYLOAD, name=request_name)
        # Add other methods (PUT, DELETE, etc.) as needed
        else:
            print(f"User {self._user_id_for_logging()}: Unsupported HTTP method: {REQUEST_METHOD}")
            # Optionally, fire a failure event
            if self.environment and self.environment.events:
                self.environment.events.request.fire(
                    request_type=REQUEST_METHOD,
                    name=request_name,
                    response_time=0,
                    response_length=0,
                    exception=NotImplementedError(f"Unsupported method: {REQUEST_METHOD} in GenericUser"),
                    context=self.context(),
                )

    @task
    def make_request_if_not_qps(self):
        """
        This task is scheduled by Locust's default mechanism.
        It should only execute if not in QPS mode (where the plugin's timer drives actions).
        """
        if LOCUST_MODE == "constant_qps" and ConstantQPSUserMixin:
            # In QPS mode, ConstantQPSUserMixin's loop calls execute_main_task_for_qps.
            # So, this Locust @task should do nothing to avoid duplicate requests or interference.
            # print(f"User {self._user_id_for_logging()}: In QPS mode, make_request_if_not_qps is a no-op.")
            pass
        else:
            # print(f"User {self._user_id_for_logging()}: Executing generic task make_request_if_not_qps.")
            self._perform_configured_request()


    def on_stop(self):
        """
        Called when a User stops.
        """
        super().on_stop() # Important for mixins like ConstantQPSUserMixin to clean up
        print(f"GenericUser instance stopped. User ID: {self._user_id_for_logging()}")


# --- Event Hooks for InfluxDB (Conceptual) ---
# These hooks are executed by the master process when using distributed mode,
# or by the local runner when running locally.

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    # This event is triggered when a new test is started.
    # For distributed runs, this runs on the master node.
    if not isinstance(environment.runner, MasterRunner): # Only run on worker nodes or local
        return
    print(f"Test started. TEST_ID: {TEST_ID}. Environment: {environment.host}")
    print("InfluxDB: Conceptual - Initialize connection here if needed.")
    print("InfluxDB: Conceptual - Store initial test metadata (e.g., test configuration).")
    # Example:
    # influx_client.write_point(measurement="test_runs",
    #                           tags={"test_id": TEST_ID, "status": "started"},
    #                           fields={"target_url": TARGET_URL, "request_method": REQUEST_METHOD, "user_count": environment.runner.target_user_count},
    #                           time=datetime.utcnow())

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    # This event is triggered when a test is stopped.
    # For distributed runs, this runs on the master node.
    if not isinstance(environment.runner, MasterRunner): # Only run on worker nodes or local
        return
    print(f"Test stopped. TEST_ID: {TEST_ID}")
    print("InfluxDB: Conceptual - Finalize test run data.")
    print("InfluxDB: Conceptual - Write summary statistics to InfluxDB.")
    # Example:
    # stats = environment.runner.stats.total
    # influx_client.write_point(measurement="test_runs",
    #                           tags={"test_id": TEST_ID, "status": "finished"},
    #                           fields={"total_requests": stats.num_requests, "total_failures": stats.num_failures, "avg_rps": stats.total_rps},
    #                           time=datetime.utcnow())

@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, context, **kwargs):
    # This event is triggered for every request made by Locust.
    # It's a good place to push detailed metrics to InfluxDB.
    # This runs on each worker node in a distributed setup.
    # To avoid overwhelming InfluxDB, consider batching points or sampling.

    # Conceptual InfluxDB Line Protocol:
    # measurement_name,tag_key1=tag_value1,tag_key2=tag_value2 field_key1=field_value1,field_key2=field_value2 timestamp

    # Example structure:
    # measurement = "locust_requests"
    # tags = {
    #     "test_id": TEST_ID,
    #     "request_name": name,
    #     "request_type": request_type, # e.g., GET, POST
    #     "status": "success" if exception is None else "failure",
    #     # "worker_id": environment.runner.client_id # If running distributed
    # }
    # fields = {
    #     "response_time": response_time, # ms
    #     "response_length": response_length, # bytes
    #     "exception": str(exception) if exception else None,
    #     # "user_count_at_request": environment.runner.user_count # Current user count
    # }
    # timestamp_ns = time.time_ns() # InfluxDB expects nanoseconds

    # print(f"InfluxDB: Conceptual - Write point: {measurement},{tags} {fields} {timestamp_ns}")
    # Actual implementation would involve an InfluxDB client and batching.
    pass # Placeholder for actual InfluxDB write logic

# --- Placeholder for other stats (could be in a separate module) ---
# Accessing Locust's runner stats:
# environment.runner.stats.total.num_requests
# environment.runner.stats.total.num_failures
# environment.runner.stats.total.avg_response_time
# environment.runner.stats.total.requests_per_sec
# environment.runner.stats.total.get_percentile("0.95") # 95th percentile response time

# Metrics to collect for InfluxDB:
# - Per request: response time, success/failure, request name, type, content length
# - Aggregated: RPS, failure rate, average/median/percentile response times, user count
# - System: CPU/memory of Locust workers (requires psutil, might be out of scope for this script)

if __name__ == "__main__":
    # This allows running the Locust file directly for local testing, e.g.:
    # locust -f backend/locust_scripts/locust_generic_test.py --host=http://localhost:5000
    # Environment variables would need to be set in the shell, e.g.:
    # export TARGET_URL="http://localhost:5000/perf-service/api/quickTestStart"
    # export REQUEST_METHOD="POST"
    # export HEADERS='{"Content-Type":"multipart/form-data; boundary=----WebKitFormBoundary7MA4YWxkTrZu0gW"}' # Example, adjust as needed
    # export PAYLOAD='{"script": "@path/to/your/script.py", "config": "{}"}' # This needs careful handling for file uploads
    print("Locust file can be run with 'locust -f backend/locust_scripts/locust_generic_test.py'")
    print("Ensure TARGET_URL, REQUEST_METHOD etc. are set as environment variables or via --host.")
    # For POST requests with multipart/form-data, the payload construction is more complex
    # and typically handled by Locust's self.client.post with the `files` parameter,
    # which is not directly settable via a simple JSON PAYLOAD env var.
    # This generic script might need adaptation for complex POSTs if run directly.
    # The Flask app will be responsible for constructing the correct Locust command and env vars.
    pass
