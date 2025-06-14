from locust import task, events, between # Import 'between' directly from locust
from locust.contrib.fasthttp import FastHttpUser
import os, csv, json, sys
from string import Template
from urllib.parse import urlencode
from jsonpath_ng import jsonpath, parse
import logging

# Get the absolute path of the directory containing this script.
# This ensures it works regardless of the current working directory from which Locust is started.
current_script_dir = os.path.dirname(os.path.abspath(__file__))
# Add this directory to Python's system path if it's not already there.
# This makes modules in this directory discoverable by absolute imports.
if current_script_dir not in sys.path:
    sys.path.insert(0, current_script_dir) # Insert at the beginning to give it priority

# Now, use an absolute import for your plugin:
from constant_throughput_plugin import ConstantThroughput

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration for Wait Time (Optional, mutually exclusive with ConstantThroughput) ---
# If TARGET_QPS is set, this wait time will be overridden by ConstantThroughput logic.
DEFAULT_WAIT_TIME_MIN = 1
DEFAULT_WAIT_TIME_MAX = 2
GLOBAL_TARGET_QPS = float(os.getenv("TARGET_QPS", 0)) # Define GLOBAL_TARGET_QPS here

# --- Custom Event for JSONPath Metrics ---
@events.init.add_listener
def _locust_init_handler(environment, **kwargs): # Renamed _ to a more descriptive name for clarity
    # Ensure environment.tags is a list (Locust typically ensures this, but good to be safe)
    if not hasattr(environment, 'tags'):
        environment.tags = []

    logger.info("Locust environment initialized.")

    # Attach a separate, explicit function to the quitting event for clarity
    # This event listener receives the environment instance as its argument
    @environment.events.quitting.add_listener
    def _locust_quitting_handler(environment, **quitting_kwargs): # Accept env and any other kwargs
        logger.info("Locust is quitting. Performing final cleanup (if any).")
        # You can add cleanup logic here, e.g., closing connections, writing final reports

class GenericUser(FastHttpUser):
    # Default wait time (will be overridden if TARGET_QPS is set)
    wait_time_min = float(os.getenv("WAIT_TIME_MIN", DEFAULT_WAIT_TIME_MIN))
    wait_time_max = float(os.getenv("WAIT_TIME_MAX", DEFAULT_WAIT_TIME_MAX))

    # wait_time will be set in the __init__ method for dynamic calculation
    wait_time = None

    data_rows = []
    payload_template = None
    payload_type = None
    endpoint = None
    method = None
    headers = {}
    reuse_data = True
    expected_json_path_values = {}
    custom_metrics_json_paths = []

    # Initialize wait_time in __init__ to access self.environment
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._load_test_config() # Load config first as it might be needed for wait_time setup
        self._configure_wait_time()


#     def _configure_wait_time(self):
#         if GLOBAL_TARGET_QPS > 0:
#             # If target QPS is set, use ConstantThroughput
#             # Pass GLOBAL_TARGET_QPS and self.environment directly
#             self.wait_time = ConstantThroughput(GLOBAL_TARGET_QPS, self.environment)
#             # Log initial calculated per-user rate, but remind it's dynamic
#             logger.info(f"Using ConstantThroughput with GLOBAL_TARGET_QPS: {GLOBAL_TARGET_QPS}. Per-user rate will dynamically adjust.")
#             logger.warning("WAIT_TIME_MIN and WAIT_TIME_MAX environment variables will be ignored when TARGET_QPS is set.")
#         else:
#             # Otherwise, use the configured wait_time_min/max
#             self.wait_time = between(self.wait_time_min, self.wait_time_max)
#             logger.info(f"Using standard wait time: between({self.wait_time_min}, {self.wait_time_max}) seconds.")

    @classmethod
    def _configure_wait_time(cls):
        if GLOBAL_TARGET_QPS > 0:
            cls.wait_time = ConstantThroughput(GLOBAL_TARGET_QPS, None)  # Env gets injected later
            # Log initial calculated per-user rate, but remind it's dynamic
            logger.info(f"Using ConstantThroughput with GLOBAL_TARGET_QPS: {GLOBAL_TARGET_QPS}. Per-user rate will dynamically adjust.")
            logger.warning("WAIT_TIME_MIN and WAIT_TIME_MAX environment variables will be ignored when TARGET_QPS is set.")
        else:
            # Otherwise, use the configured wait_time_min/max
            cls.wait_time = between(cls.wait_time_min, cls.wait_time_max)
            logger.info(f"Using standard wait time: between({cls.wait_time_min}, {cls.wait_time_max}) seconds.")

    @classmethod
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls._configure_wait_time()


    def _load_test_config(self):
        data_file = os.getenv("DATA_FILE")
        payload_template_path = os.getenv("PAYLOAD_TEMPLATE")
        self.payload_type = os.getenv("PAYLOAD_TYPE", "json").lower()
        self.endpoint = os.getenv("ENDPOINT", "/")
        self.method = os.getenv("METHOD", "POST").upper()
        headers_env = os.getenv("HEADERS")
        self.reuse_data = os.getenv("REUSE_DATA", "true").lower() == "true"

        # --- Error Handling for Headers ---
        if headers_env:
            try:
                self.headers = json.loads(headers_env)
                logger.info(f"Loaded custom headers: {self.headers}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse HEADERS environment variable as JSON. Value: '{headers_env}'")
                self.headers = {} # Ensure it's an empty dict if parsing fails
            except Exception as e:
                logger.error(f"An unexpected error occurred while parsing HEADERS: {e}")
                self.headers = {}

        # --- Error Handling for Data File ---
        if not data_file:
            logger.warning("DATA_FILE environment variable not set. No data will be used for requests.")
        elif not os.path.exists(data_file):
            logger.error(f"DATA_FILE '{data_file}' not found. Please check the path.")
            self.environment.runner.quit()
        else:
            try:
                if data_file.endswith(".csv"):
                    with open(data_file, newline='') as f:
                        self.data_rows = list(csv.DictReader(f))
                    logger.info(f"Loaded {len(self.data_rows)} rows from CSV: {data_file}")
                elif data_file.endswith(".json"):
                    with open(data_file) as f:
                        self.data_rows = json.load(f)
                    logger.info(f"Loaded {len(self.data_rows)} entries from JSON: {data_file}")
                else:
                    logger.error(f"Unsupported data file type: {data_file}. Only .csv and .json are supported.")
                    self.environment.runner.quit()
            except Exception as e:
                logger.error(f"Error loading data file '{data_file}': {e}")
                self.environment.runner.quit()

        # --- Error Handling for Payload Template ---
        if not payload_template_path:
            logger.warning("PAYLOAD_TEMPLATE environment variable not set. Requests will be sent without templated bodies.")
            self.payload_template = Template("") # Empty template
        elif not os.path.exists(payload_template_path):
            logger.error(f"PAYLOAD_TEMPLATE '{payload_template_path}' not found. Please check the path.")
            self.environment.runner.quit()
        else:
            try:
                with open(payload_template_path) as f:
                    self.payload_template = Template(f.read())
                logger.info(f"Loaded payload template from: {payload_template_path}")
            except Exception as e:
                logger.error(f"Error loading payload template '{payload_template_path}': {e}")
                self.environment.runner.quit()

        # --- Load JSONPath Assertions ---
        expected_json_path_value_str = os.getenv("EXPECTED_JSON_PATH_VALUE")
        if expected_json_path_value_str:
            try:
                self.expected_json_path_values = json.loads(expected_json_path_value_str)
                logger.info(f"Loaded JSONPath assertions: {self.expected_json_path_values}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse EXPECTED_JSON_PATH_VALUE. Expected JSON string. Got: '{expected_json_path_value_str}'")
            except Exception as e:
                logger.error(f"Error processing EXPECTED_JSON_PATH_VALUE: {e}")

        # --- Load Custom Metrics JSONPaths ---
        custom_metrics_json_path_str = os.getenv("CUSTOM_METRICS_JSON_PATH")
        if custom_metrics_json_path_str:
            try:
                self.custom_metrics_json_paths = json.loads(custom_metrics_json_path_str)
                if not isinstance(self.custom_metrics_json_paths, list):
                    logger.warning("CUSTOM_METRICS_JSON_PATH should be a JSON list of JSONPath strings. Ignoring.")
                    self.custom_metrics_json_paths = []
                logger.info(f"Loaded custom metrics JSONPaths: {self.custom_metrics_json_paths}")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse CUSTOM_METRICS_JSON_PATH. Expected JSON list. Got: '{custom_metrics_json_path_str}'")
            except Exception as e:
                logger.error(f"Error processing CUSTOM_METRICS_JSON_PATH: {e}")


    @task
    def execute_request(self):
        current_data_row = {}
        if self.data_rows:
            try:
                current_data_row = self.data_rows.pop(0)
                if self.reuse_data:
                    self.data_rows.append(current_data_row)
            except IndexError:
                logger.warning("Data rows exhausted. If REUSE_DATA is 'false', tasks may idle.")
                return
        elif not self.data_rows and (self.payload_template and "${" in self.payload_template.template):
            logger.warning("No data rows loaded, but payload template appears to expect variables. Request might fail or send incomplete data.")

        body_str = self.payload_template.safe_substitute(current_data_row)
        payload, content_type = self._prepare_payload(body_str)

        full_headers = self.headers.copy()
        if content_type:
            full_headers["Content-Type"] = content_type

        req_method = getattr(self.client, self.method.lower())
        req_args = {
            "headers": full_headers,
            "catch_response": True,
        }
        if payload:
            req_args.update(payload)

        print("::::::::::::::CCCCCCCC::::::::::::::")
        print(payload)
        print("req_args before sending:", req_args)

        with req_method(self.endpoint, **req_args) as response:
            print("response.status_code::::::::::::::::::::::", response.status_code)
            print(f"Response Body (raw bytes): {response.headers.get('Content-Length')}")
            if 200 <= response.status_code < 300:
                content_type = response.headers.get("Content-Type", "").lower()

                if "application/json" in content_type:
                    self._handle_json_response(response)
                elif "text/" in content_type or "application/xml" in content_type:
                    self._handle_text_response(response, content_type)
                elif "image/" in content_type or "application/octet-stream" in content_type or "application/pdf" in content_type:
                    self._handle_binary_response(response, content_type)
                elif response.status_code == 204: # No Content
                    self._handle_no_content_response(response)
                else:
                    # Fallback for unknown content types
                    self._handle_unknown_content_type(response, content_type)
            else:
                response.failure(f"❌ HTTP {response.status_code} - {response.text}")
                logger.error(f"Request failed: {self.method} {self.endpoint} - {response.status_code} - {response.text[:200]}")

    def _handle_json_response(self, response):
        """Handles successful HTTP responses, including custom assertions and metrics."""
        is_success = True
        failure_message = []
        response_json = {}

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            if self.expected_json_path_values or self.custom_metrics_json_paths:
                is_success = False
                failure_message.append("Response was not valid JSON, cannot apply JSONPath assertions/metrics.")
        except Exception as e:
            is_success = False
            failure_message.append(f"Error parsing response JSON: {e}")

        # --- Custom JSONPath Assertions ---
        if is_success and self.expected_json_path_values:
            for json_path_str, expected_value in self.expected_json_path_values.items():
                try:
                    jsonpath_expr = parse(json_path_str)
                    matches = jsonpath_expr.find(response_json)

                    if not matches:
                        is_success = False
                        failure_message.append(f"JSONPath '{json_path_str}' found no matches.")
                        break

                    actual_value = matches[0].value
                    if actual_value != expected_value:
                        is_success = False
                        failure_message.append(f"JSONPath '{json_path_str}': Expected '{expected_value}', got '{actual_value}'.")
                        break
                except Exception as e:
                    is_success = False
                    failure_message.append(f"Error evaluating JSONPath '{json_path_str}': {e}")
                    break

        # --- Custom Metrics from JSONPath ---
        if is_success and self.custom_metrics_json_paths and response_json:
            for json_path_str in self.custom_metrics_json_paths:
                try:
                    jsonpath_expr = parse(json_path_str)
                    matches = jsonpath_expr.find(response_json)
                    for match in matches:
                        metric_name = f"jsonpath.{json_path_str.replace('$', '').replace('.', '_').replace('[', '_').replace(']', '').strip('_')}"

                        # Corrected: Pass match.value to response_time (if numeric) for plotting
                        # and pass the original 'response' object as the response.
                        events.request.fire(
                            request_type="JSONPath_Metric",
                            name=metric_name,
                            response_time=match.value if isinstance(match.value, (int, float)) else 0, # Metric value as response_time
                            response_length=0,
                            exception=None,
                            context=self.environment.context,
                            url=self.endpoint,
                            response=response # Pass the actual HTTP response object
                        )
                        logger.debug(f"Reported custom metric '{metric_name}': {match.value}")
                except Exception as e:
                    logger.error(f"Error extracting custom metric from JSONPath '{json_path_str}': {e}")

        if is_success:
            response.success()
        else:
            response.failure(f"❌ JSON Assertion Failed: {' '.join(failure_message)}")
    def _handle_text_response(self, response, content_type):
        """Handles responses with Content-Type: text/* or application/xml"""
        try:
            text_content = response.text
            # Perform assertions on text content (e.g., search for keywords)
            if "Welcome to our page" in text_content or "Success" in text_content:
                response.success(f"{content_type}: Expected text found")
            else:
                response.failure(f"{content_type}: Expected text not found. Body: {text_content[:200]}...")
        except Exception as e:
            response.failure(f"{content_type}: Error processing text: {e} - Body: {response.text[:200]}...")

    def _handle_binary_response(self, response, content_type):
        """Handles responses with Content-Type: image/*, application/octet-stream, etc."""
        binary_content = response.content # Use .content for raw bytes
        if len(binary_content) > 0:
            # You might check file size, or perform a simple checksum/magic number check
            # For complex validation of binary files, you might save and then process
            # For performance testing, often just verifying length/existence is enough.
            logger.info(f"{content_type}: Binary data received (length: {len(binary_content)} bytes)")
            response.success()
            # Example: If it's an image, you might try to use PIL/Pillow to verify
            # from PIL import Image
            # try:
            #     img = Image.open(io.BytesIO(binary_content))
            #     if img.width > 0 and img.height > 0:
            #         response.success(f"{content_type}: Image received ({img.width}x{img.height})")
            #     else:
            #         response.failure(f"{content_type}: Invalid image dimensions")
            # except Exception as img_e:
            #     response.failure(f"{content_type}: Failed to parse image: {img_e}")
        else:
            response.failure(f"{content_type}: Empty binary response")

    def _handle_no_content_response(self, response):
        """Handles 204 No Content responses"""
        if not response.content: # Ensure there's no body
            logger.info("204 No Content: As expected")
            response.success()
        else:
            response.failure(f"204 No Content: Unexpected body present. Length: {len(response.content)}")

    def _handle_unknown_content_type(self, response, content_type):
        """Handles content types not explicitly covered"""
        response.failure(f"Unknown Content-Type ({content_type}) - Status: {response.status_code} - Body: {response.text[:200]}...")

    def _handle_error_response(self, response):
        """Handles non-2xx status codes (e.g., 4xx, 5xx)"""
        # You can define specific failure messages based on status code
        if response.status_code == 404:
            response.failure(f"Error 404: Not Found")
        elif response.status_code == 500:
            response.failure(f"Error 500: Internal Server Error - {response.text[:200]}...")
        else:
            response.failure(f"Unexpected status code: {response.status_code} - Body: {response.text[:200]}...")


    def _prepare_payload(self, body_str):
        """
        Returns tuple (payload_dict, content_type) for the FastHttpUser client.
        """
        if self.payload_type == "json":
            try:
                return {"json": json.loads(body_str)}, "application/json"
            except json.JSONDecodeError:
                logger.warning(f"Payload type is 'json' but body is not valid JSON. Sending as raw data. Body: {body_str[:100]}")
                return {"data": body_str}, "application/json"
        elif self.payload_type == "form":
            try:
                return {"data": urlencode(json.loads(body_str))}, "application/x-www-form-urlencoded"
            except json.JSONDecodeError:
                logger.warning(f"Payload type is 'form' but body is not valid JSON for form encoding. Sending as raw data. Body: {body_str[:100]}")
                return {"data": body_str}, "application/x-www-form-urlencoded"
        elif self.payload_type == "text":
            return {"data": body_str}, "text/plain"
        elif self.payload_type == "binary":
            binary_file_path = os.getenv("PAYLOAD_TEMPLATE")
            with open(binary_file_path, "rb") as f:
                binary_data = f.read()
            return {"data": binary_data}, "application/octet-stream"
#             return {"data": body_str.encode("utf-8")}, "application/octet-stream"
        else:
            logger.warning(f"Unknown payload type '{self.payload_type}'. Sending as raw data with no explicit Content-Type.")
            return {"data": body_str}, None