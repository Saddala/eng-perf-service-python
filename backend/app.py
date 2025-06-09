from flask import Flask, request, jsonify
import os
import subprocess
import uuid
import json
import shutil # For operations like ensuring directory exists, or cleaning up

app = Flask(__name__)

# Ensure the base directory for test results exists
BASE_TEST_RESULTS_DIR = os.path.join(os.getcwd(), "backend", "test_results")
os.makedirs(BASE_TEST_RESULTS_DIR, exist_ok=True)

LOCUST_SCRIPT_PATH = os.path.join(os.getcwd(), "backend", "locust_scripts", "locust_generic_test.py")

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

        app.logger.info(f"[{test_id}][{test_type_from_url}] Current working directory: {os.getcwd()}")
        app.logger.info(f"[{test_id}][{test_type_from_url}] BASE_TEST_RESULTS_DIR: {BASE_TEST_RESULTS_DIR}")
        app.logger.info(f"[{test_id}][{test_type_from_url}] test_run_dir created (or confirmed exists): {test_run_dir}")

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

        app.logger.info(f"[{test_id}][{test_type_from_url}] Locust process started with PID: {process.pid}. Output logged to {log_file_path}")

        response_data = {
            "message": f"Test ({test_type_from_url}) started successfully",
            "test_id": test_id,
            "results_dir": test_run_dir,
            "locust_log_file": os.path.join(test_run_dir, "locust.log"),
            "html_report": os.path.join(test_run_dir, "report.html"),
            "metrics_file": locust_env["INFLUX_LINE_PROTOCOL_FILE_PATH"]
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


if __name__ == '__main__':
    # For local development:
    # The default Flask port is 5000. Ensure this matches what frontend expects if any.
    # Debug mode should be False in production.
    app.run(debug=True, host='0.0.0.0', port=5001) # Changed port to 5001 for clarity


@app.route('/perf-service/api/results/<string:test_id>/live', methods=['GET'])
def get_live_results(test_id):
    try:
        app.logger.info(f"[{test_id}] get_live_results: Current working directory: {os.getcwd()}")
        app.logger.info(f"[{test_id}] get_live_results: BASE_TEST_RESULTS_DIR: {BASE_TEST_RESULTS_DIR}")
        test_run_dir = os.path.join(BASE_TEST_RESULTS_DIR, test_id)
        app.logger.info(f"[{test_id}] get_live_results: Attempting to access test_run_dir: {test_run_dir}")

        dir_exists = os.path.isdir(test_run_dir)
        app.logger.info(f"[{test_id}] get_live_results: os.path.isdir({test_run_dir}) check result: {dir_exists}")
        if not dir_exists:
            return jsonify({"error": "Test ID not found or results directory does not exist.", "test_id": test_id}), 404

        # Locust with --json and --headless prints JSON stats to stdout,
        # which we redirected to flask_locust_runner.log.
        locust_runner_log_path = os.path.join(test_run_dir, "flask_locust_runner.log")

        if not os.path.exists(locust_runner_log_path):
            return jsonify({"message": "Log file not yet created. Test might be initializing.", "test_id": test_id}), 202 # Accepted, but not ready

        last_stats_json = None
        try:
            with open(locust_runner_log_path, 'r') as f:
                for line in reversed(list(f)): # Read lines in reverse
                    line = line.strip()
                    if line.startswith('{') and line.endswith('}'):
                        try:
                            # Validate if it's actual JSON, as other log messages might exist
                            parsed_json = json.loads(line)
                            # Check for a key that indicates it's a Locust stats summary
                            if "current_rps" in parsed_json or "user_count" in parsed_json:
                                last_stats_json = parsed_json
                                break # Found the last complete JSON stats line
                        except json.JSONDecodeError:
                            continue # Not a valid JSON line, try the previous one
        except Exception as e:
            app.logger.error(f"[{test_id}] Error reading or parsing log file {locust_runner_log_path}: {e}")
            return jsonify({"error": f"Error reading or processing log file: {str(e)}", "test_id": test_id}), 500

        if last_stats_json:
            # Structure of last_stats_json can vary slightly based on Locust version and test type.
            # Common fields include: current_rps, current_fail_per_sec, user_count, stats_total, stats (list of dicts)
            # We might need to adapt what we extract based on typical output.
            # For example, the detailed stats per endpoint are often in a list under "stats"
            # and overall stats might be in "stats_total". If --json is used, it's usually a flatter structure.

            # Example extraction (adapt as needed based on actual Locust JSON output format):
            relevant_stats = {
                "test_id": test_id,
                "user_count": last_stats_json.get("user_count"),
                "current_rps": last_stats_json.get("current_rps"),
                "current_fail_per_sec": last_stats_json.get("current_fail_per_sec"),
                "total_requests": last_stats_json.get("stats_total", {}).get("num_requests") if "stats_total" in last_stats_json else last_stats_json.get("total_requests"),
                "total_failures": last_stats_json.get("stats_total", {}).get("num_failures") if "stats_total" in last_stats_json else last_stats_json.get("total_failures"),
                "response_times_avg": {}, # Placeholder for average response times per endpoint
                "response_times_percentiles": {}, # Placeholder for percentiles
                "errors": last_stats_json.get("errors", []), # List of errors
                "state": last_stats_json.get("state") # e.g., "running", "spawning", "stopped"
            }

            # Extracting response times:
            # The structure can be flat if stats are simple, or nested if detailed.
            # If stats are in a list like "stats": [{"name": "GET /path", "avg_response_time": X, ...}, ...]
            if "stats" in last_stats_json and isinstance(last_stats_json["stats"], list):
                for stat_entry in last_stats_json["stats"]:
                    name = stat_entry.get("name", "Aggregated")
                    if name == "Aggregated" and "stats_total" in last_stats_json : # Prefer stats_total for overall if available
                        relevant_stats["response_times_avg"]["Total"] = last_stats_json["stats_total"].get("avg_response_time")
                        # Percentiles might be directly in stats_total or need calculation from raw data (not available here)
                        for p_key, p_val in last_stats_json["stats_total"].get("percentiles", {}).items():
                             relevant_stats["response_times_percentiles"].setdefault("Total", {})[p_key] = p_val
                    else:
                        relevant_stats["response_times_avg"][f"{stat_entry.get('method', '')} {name}"] = stat_entry.get("avg_response_time")
                        # Extract percentiles if available per endpoint
                        for p_key, p_val in stat_entry.get("percentiles", {}).items():
                             relevant_stats["response_times_percentiles"].setdefault(f"{stat_entry.get('method', '')} {name}", {})[p_key] = p_val

            # Fallback for simpler/older Locust JSON formats or direct stats
            elif "response_times" in last_stats_json and isinstance(last_stats_json["response_times"], dict):
                 for name, times in last_stats_json["response_times"].items():
                     relevant_stats["response_times_avg"][name] = times.get("avg_response_time")
                     # Add percentiles if they exist in this structure
                     # This part is highly dependent on Locust's JSON output format

            # If stats_total is present, it's usually the most comprehensive source for totals
            if "stats_total" in last_stats_json:
                st = last_stats_json["stats_total"]
                relevant_stats["total_requests"] = st.get("num_requests")
                relevant_stats["total_failures"] = st.get("num_failures")
                relevant_stats["response_times_avg"]["Total"] = st.get("avg_response_time")
                percentiles_total = {}
                if "min_response_time" in st: percentiles_total["min"] = st.get("min_response_time")
                if "max_response_time" in st: percentiles_total["max"] = st.get("max_response_time")
                if "median_response_time" in st: percentiles_total["median (50th)"] = st.get("median_response_time")
                # Locust's --json output might not have detailed percentiles like 90th, 95th in stats_total directly in older versions.
                # The `_stats_history.csv` or HTML report would have more.
                # For live stats, this might be limited.
                if "percentiles" in st: # Newer versions might include this
                     percentiles_total.update(st["percentiles"])
                if percentiles_total:
                    relevant_stats["response_times_percentiles"]["Total"] = percentiles_total


            return jsonify(relevant_stats), 200
        else:
            return jsonify({"message": "No valid stats JSON found in the log file yet.", "test_id": test_id}), 202

    except FileNotFoundError:
        return jsonify({"error": "Test results or log file not found.", "test_id": test_id}), 404
    except Exception as e:
        app.logger.error(f"[{test_id}] Unexpected error in /results/{test_id}/live: {str(e)}")
        return jsonify({"error": f"An unexpected error occurred: {str(e)}", "test_id": test_id}), 500
