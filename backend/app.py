from flask import Flask, request, jsonify, current_app, Response
import time
import os
import subprocess
import uuid
import json
import signal
import psutil

from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# origins = [
#     "http://localhost:3001",
#     "http://your-production-frontend-domain.com"
# ]
#
# CORS(app, origins=origins, supports_credentials=True)

# Ensure the base directory for test results exists
BASE_TEST_RESULTS_DIR = os.path.join(os.getcwd(), "test_results")
os.makedirs(BASE_TEST_RESULTS_DIR, exist_ok=True)

LOCUST_SCRIPT_PATH = os.path.join(os.getcwd(), "locust_scripts", "locust_generic_test.py")

# if __name__ == '__main__':
#     app.run(port=5001)

# Basic route to check if the app is running
@app.route('/')
def home():
    return "Flask app is running!"

def _start_test_run(test_type_from_url="generic"):
    test_id = "" # Initialize test_id to ensure it's available in the outermost catch block
    try:
        test_id = str(uuid.uuid4())
        test_run_dir = os.path.join(BASE_TEST_RESULTS_DIR, test_id)
        os.makedirs(test_run_dir, exist_ok=True)

        app.logger.info(f"[{test_id}][{test_type_from_url}] New test run initiated. Directory: {test_run_dir}")
        app.logger.info(f"[{test_id}][{test_type_from_url}] Received form data: {request.form}")
        app.logger.info(f"[{test_id}][{test_type_from_url}] Received files: {request.files}")

        # --- File Handling & Environment Variable Preparation ---
        locust_env = os.environ.copy()
        form_data = request.form

        # Handle envVarsFile first to merge into locust_env
        if 'envVarsFile' in request.files:
            file = request.files['envVarsFile']
            if file.filename != '':
                filepath = os.path.join(test_run_dir, "env_vars_upload.json")
                file.save(filepath)
                app.logger.info(f"[{test_id}][{test_type_from_url}] Saved envVarsFile to {filepath}")
                try:
                    with open(filepath, 'r') as f:
                        env_vars_from_file = json.load(f)
                    if isinstance(env_vars_from_file, dict):
                        locust_env.update(env_vars_from_file)
                    else:
                        app.logger.warning(f"[{test_id}][{test_type_from_url}] envVarsFile was not a JSON object. Ignoring.")
                except json.JSONDecodeError:
                    app.logger.warning(f"[{test_id}][{test_type_from_url}] Failed to parse envVarsFile as JSON. Ignoring.")
                except Exception as e:
                    app.logger.error(f"[{test_id}][{test_type_from_url}] Error processing envVarsFile: {e}")

        # Basic env vars from form
        locust_env["TEST_ID"] = test_id
        locust_env["TARGET_HOST"] = form_data.get("host", "http://localhost:8080") # Used for --host
        locust_env["ENDPOINT"] = form_data.get("url", "/") # Renamed from TARGET_PATH
        locust_env["METHOD"] = form_data.get("method", "GET").upper() # Renamed from REQUEST_METHOD
        locust_env["HEADERS"] = form_data.get("headers", "{}") # This will be read by locust script
        # QUERY_PARAMS is not standard in the new script, consider removing or adapting script
        # locust_env["QUERY_PARAMS"] = form_data.get("query_params", "{}")

        # Handle PayloadType and PAYLOAD_TEMPLATE
        payload_type_from_form = form_data.get("payloadType", "json").lower()
        locust_env["PAYLOAD_TYPE"] = payload_type_from_form
        inline_payload_types = ["json", "text", "form-urlencoded", "application/x-www-form-urlencoded"]

        if 'payloadTemplateFile' in request.files and request.files['payloadTemplateFile'].filename != '':
            file = request.files['payloadTemplateFile']
            # Use a consistent name or make it unique if clashes are possible with dataFile
            filename = "payload_template_from_file" + os.path.splitext(file.filename)[1] if file.filename else "payload_template_from_file.txt"
            filepath = os.path.join(test_run_dir, filename)
            file.save(filepath)
            locust_env["PAYLOAD_TEMPLATE"] = filepath
            app.logger.info(f"[{test_id}][{test_type_from_url}] Saved payloadTemplateFile to {filepath} and set as PAYLOAD_TEMPLATE.")
        elif payload_type_from_form in inline_payload_types:
            inline_payload_content = form_data.get("payload", form_data.get("inlinePayloadContent", ""))
            temp_payload_filename = f"inline_payload_for_{test_id}.txt"
            temp_payload_filepath = os.path.join(test_run_dir, temp_payload_filename)
            with open(temp_payload_filepath, 'w') as f:
                f.write(inline_payload_content)
            locust_env["PAYLOAD_TEMPLATE"] = temp_payload_filepath
            app.logger.info(f"[{test_id}][{test_type_from_url}] Saved inline payload to {temp_payload_filepath} and set as PAYLOAD_TEMPLATE.")
        else:
            app.logger.info(f"[{test_id}][{test_type_from_url}] No payloadTemplateFile uploaded and payloadType ('{payload_type_from_form}') is not for inline. PAYLOAD_TEMPLATE may not be set unless from envVarsFile.")

        # Handle dataFile
        if 'dataFile' in request.files and request.files['dataFile'].filename != '':
            file = request.files['dataFile']
            # Using original filename, ensure it's sanitized if necessary, though os.path.join is safe
            filepath = os.path.join(test_run_dir, file.filename)
            file.save(filepath)
            locust_env["DATA_FILE"] = filepath
            app.logger.info(f"[{test_id}][{test_type_from_url}] Saved dataFile to {filepath}")

        # Primary determination of LOCUST_MODE is from 'load_type' in form data (for QPS)
        load_type_form = form_data.get("load_type", "RAMP_TEST").upper()
        app.logger.info(f"[{test_id}][{test_type_from_url}] load_type from form: {load_type_form}")

        # TARGET_QPS is global in the new locust script, set via env var.
        # LOCUST_MODE is not directly used by new script's GenericUser for QPS enabling,
        # but QPS is enabled if TARGET_QPS > 0.
        # We can still set TARGET_QPS env var based on form data.
        if load_type_form == "QPS_TEST":
            locust_env["TARGET_QPS"] = form_data.get("targetQps", os.getenv("TARGET_QPS", "0")) # Get from form, fallback to existing env, then "0"
        else:
            # If not QPS_TEST, ensure TARGET_QPS is "0" or not set to disable QPS in script
            locust_env["TARGET_QPS"] = "0"
            # locust_env.pop("TARGET_QPS", None) # Alternative: remove it

        # INFLUX_LINE_PROTOCOL_FILE_PATH is not standard in new script.
        # If needed, script must be adapted or this can be passed via envVarsFile.
        # locust_env["INFLUX_LINE_PROTOCOL_FILE_PATH"] = os.path.join(test_run_dir, "metrics.influx")

        # --- Locust Command Construction ---
        # Note: TARGET_HOST is now passed via --host
        cmd = [
            "locust",
            "-f", LOCUST_SCRIPT_PATH,
            "--host", locust_env["TARGET_HOST"], # Added --host
            "--headless",
            "--logfile", os.path.join(test_run_dir, "locust.log"),
            "--json", # Ensure script outputting JSON stats if this is used for live results
            "--html", os.path.join(test_run_dir, "report.html")
        ]

        users = form_data.get("users", "1")
        spawn_rate = form_data.get("spawnRate", "1")
        run_time = form_data.get("duration")

        cmd.extend(["--users", users])
        cmd.extend(["--spawn-rate", spawn_rate])
        if run_time:
            cmd.extend(["--run-time", run_time])

        app.logger.info(f"[{test_id}][{test_type_from_url}] Constructed Locust command: {' '.join(cmd)}")
        # Avoid logging sensitive parts of locust_env if any in future. For now, it's mostly config.
        # app.logger.debug(f"[{test_id}][{test_type_from_url}] With environment: {json.dumps(locust_env, indent=2)}")

        log_file_path = os.path.join(test_run_dir, "flask_locust_runner.log")
        with open(log_file_path, 'wb') as log_file:
            process = subprocess.Popen(cmd, env=locust_env, stdout=log_file, stderr=subprocess.STDOUT)

        pid_file = os.path.join(test_run_dir, "locust.pid")
        with open(pid_file, "w") as f:
            f.write(str(process.pid))

        app.logger.info(f"[{test_id}][{test_type_from_url}] Locust process started with PID: {process.pid}. Output logged to {log_file_path}")

        response_data = {
            "message": f"Test ({test_type_from_url}) started successfully",
            "test_id": test_id,
            "results_dir": test_run_dir,
            "locust_log_file": os.path.join(test_run_dir, "locust.log"),
            "html_report": os.path.join(test_run_dir, "report.html")
            # "metrics_file": locust_env["INFLUX_LINE_PROTOCOL_FILE_PATH"]
        }
        return jsonify(response_data), 200

    except FileNotFoundError as fnfe:
        app.logger.error(f"[{test_id or 'N/A'}][{test_type_from_url}] File not found error: {str(fnfe)}")
        return jsonify({"error": f"File operation error: {str(fnfe)}", "test_id": test_id or 'N/A'}), 500
    except PermissionError as pe:
        app.logger.error(f"[{test_id or 'N/A'}][{test_type_from_url}] Permission error: {str(pe)}")
        return jsonify({"error": f"File permission error: {str(pe)}", "test_id": test_id or 'N/A'}), 500
    except subprocess.SubprocessError as spe:
        app.logger.error(f"[{test_id or 'N/A'}][{test_type_from_url}] Subprocess error: {str(spe)}")
        return jsonify({"error": f"Failed to start Locust process: {str(spe)}", "test_id": test_id or 'N/A'}), 500
    except Exception as e:
        app.logger.error(f"[{test_id or 'N/A'}][{test_type_from_url}] Error in _start_test_run: {str(e)}")
        return jsonify({"error": "An unexpected error occurred", "details": str(e), "test_id": test_id or 'N/A'}), 500

# Original quickTestStart, now calls the refactored function
@app.route('/perf-service/api/quickTestStart', methods=['POST'])
def quick_test_start():
    return _start_test_run(test_type_from_url="quickTestStart_generic")

# New specific routes
@app.route('/perf-service/api/ramp-up/start', methods=['POST'])
def start_ramp_up_test():
    return _start_test_run(test_type_from_url="ramp-up")

@app.route('/perf-service/api/qps/start', methods=['POST'])
def start_qps_test():
    return _start_test_run(test_type_from_url="qps")

@app.route('/perf-service/api/spike/start', methods=['POST'])
def start_spike_test():
    # For now, spike, soak, stress, data-driven will use the same core logic.
    # The `load_type` from form data will differentiate if Locust script has specific logic.
    # Future: _start_test_run could have more branches based on test_type_from_url if needed.
    return _start_test_run(test_type_from_url="spike")

@app.route('/perf-service/api/soak/start', methods=['POST'])
def start_soak_test():
    return _start_test_run(test_type_from_url="soak")

@app.route('/perf-service/api/stress/start', methods=['POST'])
def start_stress_test():
    return _start_test_run(test_type_from_url="stress")

@app.route('/perf-service/api/data-driven/start', methods=['POST'])
def start_data_driven_test():
    return _start_test_run(test_type_from_url="data-driven")

@app.route('/perf-service/api/generic/start', methods=['POST'])
def start_generic_test():
    return _start_test_run(test_type_from_url="generic")

@app.route('/perf-service/api/results/live', methods=['GET'])
def get_live_results2():
    app.logger.error(f"[] Error reading or parsing log file ")
    return jsonify("success"), 200

@app.route('/perf-service/api/results/<string:test_id>/live', methods=['GET'])
def get_live_results(test_id):
    try:
        if not BASE_TEST_RESULTS_DIR:
            current_app.logger.error(f"[{test_id}] SSE: BASE_TEST_RESULTS_DIR is not configured in Flask app!")

            def config_error_stream():
                error_json = json.dumps(
                    {"error": "Server configuration error: BASE_TEST_RESULTS_DIR not set.", "test_id": test_id,
                     "sse_status": "error_server_config"})
                yield f"event: error\ndata: {error_json}\n\n"

            return Response(config_error_stream(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})

        test_run_dir = os.path.join(BASE_TEST_RESULTS_DIR, test_id)
        locust_runner_log_path = os.path.join(test_run_dir, "flask_locust_runner_metrics.log")

        if not os.path.isdir(test_run_dir):
            current_app.logger.error(f"[{test_id}] SSE: Test ID directory not found: {test_run_dir}")

            def error_stream_dir_not_found():
                error_json = json.dumps(
                    {"error": "Test ID not found or results directory does not exist.", "test_id": test_id,
                     "sse_status": "error_no_directory"})
                yield f"event: error\ndata: {error_json}\n\n"

            return Response(error_stream_dir_not_found(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})

        initial_log_exists = os.path.exists(locust_runner_log_path)
        if not initial_log_exists:
            current_app.logger.warning(
                f"[{test_id}] SSE: Log file not yet created: {locust_runner_log_path}. Test might be initializing.")

        # Define the generator for the event stream
        def event_stream():
            # test_id = os.environ.get('test_id')
            # test_run_dir = os.path.join(BASE_TEST_RESULTS_DIR, test_id)
            # locust_runner_log_path = os.path.join(test_run_dir, "flask_locust_runner_metrics.log")
            last_file_size = 0
            # initial_log_exists = os.path.exists(locust_runner_log_path)
            print("******** event_stream **************")

            if not initial_log_exists:
                init_msg = {"message": "Log file not yet created. Monitoring for test start.", "test_id": test_id,
                            "status": "initializing"}
                yield f"event: status\ndata: {json.dumps(init_msg)}\n\n"

            try:
                try:
                    curr_logger = current_app.logger
                except RuntimeError:
                    curr_logger = None
                if curr_logger:
                    curr_logger.info(f"[{test_id}] SSE: Starting event stream for {locust_runner_log_path}")

                file_missing_reported = not initial_log_exists

                while True:
                    if not os.path.exists(locust_runner_log_path):
                        if not file_missing_reported:
                            msg = {"message": "Log file not found. Continuing to monitor.", "test_id": test_id,
                                   "status": "waiting_for_log"}
                            yield f"event: status\ndata: {json.dumps(msg)}\n\n"
                            file_missing_reported = True
                        time.sleep(2)
                        continue

                    file_missing_reported = False
                    current_file_size = os.path.getsize(locust_runner_log_path)

                    if current_file_size == last_file_size:
                        time.sleep(1)
                        continue

                    new_lines_buffer = []
                    with open(locust_runner_log_path, 'r') as f:
                        if current_file_size < last_file_size:
                            f.seek(0)
                        else:
                            f.seek(last_file_size)
                        for line in f:
                            new_lines_buffer.append(line.strip())

                    last_file_size = current_file_size

                    for line_content in new_lines_buffer:
                        if line_content.startswith('{') and line_content.endswith('}'):
                            try:
                                parsed_json = json.loads(line_content)
                                if "event" in parsed_json:
                                    event_type = parsed_json.get("event", "update")
                                    yield f"event: {event_type}\ndata: {json.dumps(parsed_json)}\n\n"

                                    current_state = parsed_json.get("state")
                                    if current_state and current_state.lower() in ["stopped", "finished", "cleanup"]:
                                        final_msg = json.dumps({
                                            "message": f"Test {current_state}.",
                                            "test_id": test_id,
                                            "state": current_state
                                        })
                                        yield f"event: test_completed\ndata: {final_msg}\n\n"
                                        return
                            except json.JSONDecodeError:
                                continue

                    time.sleep(0.5)

            except GeneratorExit:
                try:
                    curr_logger = current_app.logger
                except RuntimeError:
                    curr_logger = None
                if curr_logger:
                    curr_logger.info(f"[{test_id}] SSE: Stream closed by client for {locust_runner_log_path}")

            except Exception as e:
                try:
                    curr_logger = current_app.logger
                except RuntimeError:
                    curr_logger = None
                if curr_logger:
                    curr_logger.error(f"[{test_id}] SSE: Error in event stream for {locust_runner_log_path}: {str(e)}",
                                      exc_info=True)
                error_json = json.dumps({"error": f"Server error in SSE stream: {str(e)}", "test_id": test_id,
                                         "sse_status": "error_streaming"})
                try:
                    yield f"event: error\ndata: {error_json}\n\n"
                except Exception:
                    pass

            finally:
                try:
                    curr_logger = current_app.logger
                except RuntimeError:
                    curr_logger = None
                if curr_logger:
                    curr_logger.info(f"[{test_id}] SSE: Ending event stream generator for {locust_runner_log_path}")

        response = Response(event_stream(), mimetype='text/event-stream')
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['Connection'] = 'keep-alive'
        response.headers['X-Accel-Buffering'] = 'no'
        return response

    except Exception as e:
        current_app.logger.error(f"[{test_id}] SSE: Critical error during initial setup for live results: {str(e)}",
                                 exc_info=True)

        def critical_setup_error_stream():
            error_json = json.dumps({"error": f"Critical server error during SSE setup: {str(e)}", "test_id": test_id,
                                     "sse_status": "error_critical_setup"})
            yield f"event: error\ndata: {error_json}\n\n"

        return Response(critical_setup_error_stream(), mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'Connection': 'keep-alive'})


@app.route('/perf-service/api/test/<string:test_id>/stop', methods=['POST'])
def stop_test(test_id):
    try:
        test_dir = os.path.join(BASE_TEST_RESULTS_DIR, test_id)
        pid_file = os.path.join(test_dir, "locust.pid")

        if not os.path.exists(pid_file):
            return jsonify({"error": "PID file not found for test_id", "test_id": test_id}), 404

        with open(pid_file, 'r') as f:
            pid = int(f.read().strip())

        if not psutil.pid_exists(pid):
            return jsonify({"error": "Process not running", "test_id": test_id}), 410

        proc = psutil.Process(pid)
        proc.send_signal(signal.SIGINT)

        return jsonify({"message": "Stop signal sent to Locust test.", "test_id": test_id}), 200

    except Exception as e:
        try:
            logger = current_app.logger
        except RuntimeError:
            logger = None
        if logger:
            logger.error(f"Failed to stop test {test_id}: {e}", exc_info=True)
        return jsonify({"error": str(e), "test_id": test_id}), 500


if __name__ == '__main__':
    # For local development:
    # The default Flask port is 5000. Ensure this matches what frontend expects if any.
    # Debug mode should be False in production.
    app.run(debug=True, host='0.0.0.0', port=5001)