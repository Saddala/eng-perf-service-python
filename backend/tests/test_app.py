import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock, mock_open
import json
import subprocess # Added import
from io import BytesIO # Added for file upload

# Add the backend directory to sys.path to allow direct import of app
import sys
import importlib # Needed for reloading locust_generic_test
# from backend.locust_scripts.locust_generic_test import GenericUser # We import the module instead
from backend.locust_scripts import locust_generic_test # Import the module to reload
from locust.env import Environment
from locust.runners import LocalRunner # For dummy runner
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app

class PerfServiceAPITestCase(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        # Create a temporary directory for test results
        self.test_dir = tempfile.mkdtemp()
        app.config['BASE_TEST_RESULTS_DIR'] = self.test_dir
        # Ensure the global BASE_TEST_RESULTS_DIR in app.py is also updated
        # This is a bit of a hack; ideally, app configuration should be more dynamic
        # For testing, we are directly patching the module-level variable
        import app as main_app_module
        main_app_module.BASE_TEST_RESULTS_DIR = self.test_dir
        # Corrected LOCUST_SCRIPT_PATH
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        main_app_module.LOCUST_SCRIPT_PATH = os.path.join(base_dir, "locust_scripts", "locust_generic_test.py")

    def tearDown(self):
        # Remove the temporary directory after tests
        shutil.rmtree(self.test_dir)

    def test_home_route(self):
        response = self.app.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.decode(), "Flask app is running!")

    @patch('subprocess.Popen')
    # @patch('os.makedirs') # Removed mock_makedirs
    @patch('uuid.uuid4')
    def test_start_generic_test_success(self, mock_uuid, mock_popen): # Removed mock_makedirs from args
        # Mock Popen to simulate successful Locust process start
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        # Mock uuid to have a predictable test_id
        mock_uuid.return_value = "test-uuid-123"

        # os.makedirs will run for real, creating subdirectories in self.test_dir

        form_data = {
            "host": "http://example.com",
            "users": "10",
            "spawnRate": "2",
            "duration": "60s"
        }
        response = self.app.post('/perf-service/api/generic/start', data=form_data)
        self.assertEqual(response.status_code, 200)
        json_response = json.loads(response.data.decode())
        self.assertEqual(json_response["message"], "Test (generic) started successfully")
        self.assertEqual(json_response["test_id"], "test-uuid-123")
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "test-uuid-123")))
        self.assertTrue(json_response["results_dir"].endswith("test-uuid-123"))

        # Verify Popen was called
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertIn("locust", args[0]) # Check if 'locust' is in the command
        self.assertIn(os.path.join(self.test_dir, "test-uuid-123", "locust.log"), args[0]) # Check logfile path

    @patch('subprocess.Popen')
    # @patch('os.makedirs') # Removed mock_makedirs
    @patch('uuid.uuid4')
    def test_start_qps_test_success(self, mock_uuid, mock_popen): # Removed mock_makedirs from args
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process
        mock_uuid.return_value = "test-uuid-qps-123"
        # os.makedirs will run for real

        form_data = {
            "host": "http://example.com",
            "load_type": "QPS_TEST",
            "targetQps": "100"
        }
        response = self.app.post('/perf-service/api/qps/start', data=form_data)
        self.assertEqual(response.status_code, 200)
        json_response = json.loads(response.data.decode())
        self.assertEqual(json_response["message"], "Test (qps) started successfully")
        self.assertEqual(json_response["test_id"], "test-uuid-qps-123")

        # Verify environment variables for QPS test
        _, kwargs = mock_popen.call_args
        self.assertEqual(kwargs['env']['LOCUST_MODE'], 'constant_qps')
        self.assertEqual(kwargs['env']['TARGET_QPS'], '100')

    @patch('subprocess.Popen')
    def test_start_test_subprocess_error(self, mock_popen):
        # Simulate a SubprocessError
        mock_popen.side_effect = subprocess.SubprocessError("Failed to start locust")

        form_data = {"host": "http://example.com"}
        response = self.app.post('/perf-service/api/generic/start', data=form_data)
        self.assertEqual(response.status_code, 500)
        json_response = json.loads(response.data.decode())
        self.assertTrue("Failed to start Locust process" in json_response["error"])

    @patch('os.path.isdir')
    def test_get_live_results_test_id_not_found(self, mock_isdir):
        mock_isdir.return_value = False # Simulate test_id directory not found
        response = self.app.get('/perf-service/api/results/nonexistent-test-id/live')
        self.assertEqual(response.status_code, 404)
        json_response = json.loads(response.data.decode())
        self.assertEqual(json_response["error"], "Test ID not found or results directory does not exist.")

    def test_get_live_results_log_file_not_yet_created(self):
        test_id = "test-id-no-log"
        test_run_dir = os.path.join(self.test_dir, test_id)
        os.makedirs(test_run_dir, exist_ok=True) # Create directory, but no log file

        response = self.app.get(f'/perf-service/api/results/{test_id}/live')
        self.assertEqual(response.status_code, 202) # Accepted, but not ready
        json_response = json.loads(response.data.decode())
        self.assertEqual(json_response["message"], "Log file not yet created. Test might be initializing.")

    @patch('builtins.open', new_callable=mock_open)
    def test_get_live_results_empty_log_file(self, mock_file_open):
        test_id = "test-id-empty-log"
        test_run_dir = os.path.join(self.test_dir, test_id)
        os.makedirs(test_run_dir, exist_ok=True)
        # Create the flask_locust_runner.log file, but it's empty
        open(os.path.join(test_run_dir, "flask_locust_runner.log"), 'w').close()

        # Mock open to return an empty file
        mock_file_open.return_value.readlines.return_value = []
        # Also need to mock os.path.exists for the log file
        with patch('os.path.exists', return_value=True):
            response = self.app.get(f'/perf-service/api/results/{test_id}/live')

        self.assertEqual(response.status_code, 202)
        json_response = json.loads(response.data.decode())
        self.assertEqual(json_response["message"], "No valid stats JSON found in the log file yet.")


    @patch('builtins.open', new_callable=mock_open)
    def test_get_live_results_success_with_stats(self, mock_file_open):
        test_id = "test-id-with-stats"
        test_run_dir = os.path.join(self.test_dir, test_id)
        os.makedirs(test_run_dir, exist_ok=True)
        log_path = os.path.join(test_run_dir, "flask_locust_runner.log")

        # Sample Locust JSON output line - corrected to have single closing brace
        stats_line = '{"current_rps": 10.5, "current_fail_per_sec": 0.1, "user_count": 50, "state": "running", "stats_total": {"num_requests": 1000, "num_failures": 10, "avg_response_time": 123.45}}'
        another_log_line = "Some other logging info"

        # Corrected mock for file content
        mock_file = MagicMock()
        # Configure for both f.readlines() and "for line in f:"
        mock_file.readlines.return_value = [another_log_line + "\n", stats_line]
        mock_file.__iter__.return_value = iter([another_log_line + "\n", stats_line])
        mock_file.__enter__.return_value = mock_file # For context manager 'with open(...)'
        mock_file.__exit__.return_value = None
        mock_file_open.return_value = mock_file

        # Mock os.path.exists to confirm log file existence
        with patch('os.path.exists', return_value=True):
            response = self.app.get(f'/perf-service/api/results/{test_id}/live')

        json_response = json.loads(response.data.decode())
        if response.status_code == 202:
            print(f"DEBUG: Received 202 with message: {json_response.get('message')}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json_response["test_id"], test_id)
        self.assertEqual(json_response["current_rps"], 10.5)
        self.assertEqual(json_response["user_count"], 50)
        self.assertEqual(json_response["total_requests"], 1000)

    # Test all start endpoints to ensure they call _start_test_run correctly
    @patch('app._start_test_run') # Patch the common function
    def test_all_start_endpoints(self, mock_start_test_run):
        # Define a dummy response for the mocked function
        mock_start_test_run.return_value = (json.dumps({"message": "Mocked success"}), 200)

        endpoints = [
            '/perf-service/api/quickTestStart',
            '/perf-service/api/ramp-up/start',
            # '/perf-service/api/qps/start', # Tested more specifically above
            '/perf-service/api/spike/start',
            '/perf-service/api/soak/start',
            '/perf-service/api/stress/start',
            '/perf-service/api/data-driven/start',
            # '/perf-service/api/generic/start' # Tested more specifically above
        ]

        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                # For POST requests, typically data is expected.
                # Even if _start_test_run is mocked, Flask routing might check content type or expect data.
                response = self.app.post(endpoint, data={"test": "data"})
                self.assertEqual(response.status_code, 200)
                # Extract the test_type_from_url from the endpoint
                # e.g. /perf-service/api/ramp-up/start -> ramp-up
                # e.g. /perf-service/api/quickTestStart -> quickTestStart_generic
                if endpoint == '/perf-service/api/quickTestStart':
                    expected_test_type = "quickTestStart_generic"
                else:
                    expected_test_type = endpoint.split('/')[-2]

                mock_start_test_run.assert_called_with(test_type_from_url=expected_test_type)

    @patch('subprocess.Popen')
    # @patch('os.makedirs') # Removed mock_makedirs
    @patch('uuid.uuid4')
    @patch('builtins.open', new_callable=mock_open) # Mock open for envVarsFile logic in app.py
    def test_start_test_with_env_vars_file(self, mock_file_open_for_env_in_app, mock_uuid, mock_popen): # mock_makedirs removed
        mock_process = MagicMock()
        mock_process.pid = 123
        mock_popen.return_value = mock_process
        mock_uuid.return_value = "test-uuid-envfile"
        # os.makedirs will run for real

        # Simulate file content for envVarsFile
        env_vars_content = '{"MY_VAR": "my_value", "OTHER_VAR": "other_value"}'
        # Create a mock file object for the upload
        mock_env_file = MagicMock()
        mock_env_file.filename = "env_vars.json"
        # This mock_open will be used by app.py when it reads the env file *after* it's saved.
        # The actual saving of the uploaded file by request.files['envVarsFile'].save(filepath)
        # is not easily mocked with builtins.open here, as it's a Werkzeug FileStorage object.
        # So, we mock the reading part.
        mock_file_open_for_env_in_app.return_value = mock_open(read_data=env_vars_content).return_value

        form_data = {
            "host": "http://example.com",
        }
        file_data = BytesIO(env_vars_content.encode('utf-8'))

        # Corrected way to pass files with test client
        # We also patch json.load as the primary way to inject the env vars,
        # bypassing the complexities of mocking the exact file save/read sequence for this specific test.
        # The builtins.open mock above is a fallback if json.load isn't the direct reader.
        with patch('json.load', return_value=json.loads(env_vars_content)) as mock_json_load:
            response = self.app.post(
                '/perf-service/api/generic/start',
                data={**form_data, 'envVarsFile': (file_data, 'env_vars.json')},
                content_type='multipart/form-data'
            )

        self.assertEqual(response.status_code, 200)
        # Ensure json.load was actually called if the file was processed
        # This depends on app.py's implementation: does it save then json.load?
        # If the test fails, it might be that json.load wasn't called as expected.
        # We'll check Popen's env vars as the ultimate proof.
        _, kwargs = mock_popen.call_args
        self.assertIn("MY_VAR", kwargs['env'])
        self.assertEqual(kwargs['env']['MY_VAR'], "my_value")
        self.assertIn("OTHER_VAR", kwargs['env'])
        self.assertEqual(kwargs['env']['OTHER_VAR'], "other_value")

    # The test_generic_user_host_attribute has been removed as the
    # GenericUser.host attribute is no longer set directly via os.getenv
    # within the class. It's now handled by Locust's HttpUser parent class
    # and typically set via CLI --host or Environment(host=...).


# New Test Class for GenericUser request construction
class TestGenericUserRequestConstruction(unittest.TestCase):
    def setUp(self):
        # Mock Locust Environment, common for all tests in this class
        self.mock_env = Environment(host="http://envhost.com", events=MagicMock())
        self.mock_env.runner = MagicMock(spec=LocalRunner)
        self.mock_env.runner.client_id = "test_runner_client_01"

        # It's good practice to ensure the module is in a known state before tests,
        # but each test will reload it with a specific environment.
        # We can reload here with some baseline defaults if necessary, or rely on
        # the first test's patched reload. For safety, let's establish a baseline.
        baseline_env = {
            "TARGET_URL": "http://baseline.com", "ENDPOINT_PATH": "/baseline",
            "REQUEST_METHOD": "GET", "HEADERS_STR": "{}", "PAYLOAD_STR": "{}",
            "LOCUST_MODE": "generic", "TARGET_QPS_STR": "1",
            "THINK_TIME_MIN_STR": "1", "THINK_TIME_MAX_STR": "1"
        }
        with patch.dict(os.environ, baseline_env, clear=True):
            importlib.reload(locust_generic_test)

    def tearDown(self):
        # Restore environment to a baseline state after each test,
        # This helps ensure no leakage between test classes if os.environ was dirtied.
        # The patch.dict in each test method handles restoration for that test's changes.
        baseline_env = {
            "TARGET_URL": "http://baseline.com", "ENDPOINT_PATH": "/baseline",
            "REQUEST_METHOD": "GET", "HEADERS_STR": "{}", "PAYLOAD_STR": "{}",
            "LOCUST_MODE": "generic", "TARGET_QPS_STR": "1",
            "THINK_TIME_MIN_STR": "1", "THINK_TIME_MAX_STR": "1"
        }
        # For safety, clear out other test keys that might have been set if a test failed mid-patch
        test_specific_keys = ["ENDPOINT_PATH", "REQUEST_METHOD", "HEADERS_STR", "PAYLOAD_STR"] # These are primary ones varied by tests
        # However, patch.dict should handle this. The main goal of tearDown here is
        # to reset locust_generic_test module's global state.
        with patch.dict(os.environ, baseline_env, clear=True):
             importlib.reload(locust_generic_test)

    def _get_configured_user_after_reload(self):
        # Assumes locust_generic_test has just been reloaded by the caller (inside patch.dict context)
        # Set class host directly for User instantiation as determined to be necessary
        locust_generic_test.GenericUser.host = self.mock_env.host
        user = locust_generic_test.GenericUser(environment=self.mock_env)
        user.client = MagicMock()
        user.client.host = self.mock_env.host
        return user

    def test_custom_get_request(self):
        custom_endpoint = "/custom/api/get"
        custom_headers_str = '{"X-Test-Header": "TestValue"}'
        env_patch = {
            "ENDPOINT_PATH": custom_endpoint,
            "REQUEST_METHOD": "GET",
            "HEADERS_STR": custom_headers_str,
            # Required defaults for module reload
            "PAYLOAD_STR": "{}", "LOCUST_MODE": "generic", "TARGET_URL": "http://log.com",
            "TARGET_QPS_STR": "1", "THINK_TIME_MIN_STR": "1", "THINK_TIME_MAX_STR": "1"
        }
        with patch.dict(os.environ, env_patch, clear=True): # clear=True is important
            importlib.reload(locust_generic_test)
            user = self._get_configured_user_after_reload()
            user._perform_configured_request()

        expected_headers = {"X-Test-Header": "TestValue"}
        expected_name = f"GET_{custom_endpoint}"
        user.client.get.assert_called_once_with(
            custom_endpoint,
            headers=expected_headers,
            name=expected_name
        )

    def test_custom_post_request(self):
        custom_endpoint = "/custom/api/post"
        payload_dict = {"data": "test"}
        payload_str = json.dumps(payload_dict)
        custom_headers_str = '{"Content-Type": "application/json"}'

        env_patch = {
            "ENDPOINT_PATH": custom_endpoint,
            "REQUEST_METHOD": "POST",
            "PAYLOAD_STR": payload_str,
            "HEADERS_STR": custom_headers_str,
            # Required defaults for module reload
            "LOCUST_MODE": "generic", "TARGET_URL": "http://log.com",
            "TARGET_QPS_STR": "1", "THINK_TIME_MIN_STR": "1", "THINK_TIME_MAX_STR": "1"
        }
        with patch.dict(os.environ, env_patch, clear=True): # clear=True
            importlib.reload(locust_generic_test)
            user = self._get_configured_user_after_reload()
            user._perform_configured_request()

        expected_headers = {"Content-Type": "application/json"}
        expected_name = f"POST_{custom_endpoint}"
        user.client.post.assert_called_once_with(
            custom_endpoint,
            headers=expected_headers,
            json=payload_dict,
            name=expected_name
        )

    def test_default_get_request(self):
        # Test that ENDPOINT_PATH uses its os.getenv default when not in the patch.
        # Other necessary vars for module load are explicitly provided.
        default_endpoint_in_script = "/default_path" # Expected default for ENDPOINT_PATH

        env_patch = {
            # ENDPOINT_PATH is deliberately omitted to test its default
            "REQUEST_METHOD": "GET",
            "HEADERS_STR": "{}", # Explicitly default for this test
            "PAYLOAD_STR": "{}", # Explicitly default for this test
            "LOCUST_MODE": "generic",
            "TARGET_URL": "http://log.com",
            "TARGET_QPS_STR": "1",
            "THINK_TIME_MIN_STR": "1",
            "THINK_TIME_MAX_STR": "1"
        }

        with patch.dict(os.environ, env_patch, clear=True):
            importlib.reload(locust_generic_test)
            user = self._get_configured_user_after_reload()
            user._perform_configured_request()

        expected_name = f"GET_{default_endpoint_in_script}"
        user.client.get.assert_called_once_with(
            default_endpoint_in_script,
            headers={},
            name=expected_name
        )


if __name__ == '__main__':
    unittest.main()
