# Performance Testing Backend Service

This project provides a backend service for running performance tests using Locust. It exposes APIs to start various types of tests and retrieve live results.

## Local Execution

### Prerequisites
- Python 3.8+
- pip

### Setup and Run
1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd <repository-directory>
   ```
2. Create a virtual environment and activate it:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```
4. Run the Flask application:
   ```bash
   python backend/app.py
   ```
   The application will be available at `http://localhost:5001`.

## Docker Execution

1. Build the Docker image:
   ```bash
   docker build -t perf-backend .
   ```
2. Run the Docker container:
   ```bash
   docker run -p 5001:5001 perf-backend
   ```
   The application will be available at `http://localhost:5001`.

## API Endpoints

- **POST /perf-service/api/quickTestStart**: Starts a generic test run.
- **POST /perf-service/api/ramp-up/start**: Starts a ramp-up test.
- **POST /perf-service/api/qps/start**: Starts a QPS (Queries Per Second) test.
- **POST /perf-service/api/spike/start**: Starts a spike test.
- **POST /perf-service/api/soak/start**: Starts a soak test.
- **POST /perf-service/api/stress/start**: Starts a stress test.
- **POST /perf-service/api/data-driven/start**: Starts a data-driven test.
- **POST /perf-service/api/generic/start**: Starts a generic test.
- **GET /perf-service/api/results/<test_id>/live**: Retrieves live results for a given test ID.

Refer to the `backend/app.py` for details on request parameters for starting tests.