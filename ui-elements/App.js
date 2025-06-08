import React, { useState, useEffect } from "react";
import LiveResultsPage from './LiveResultsPage'; // Import LiveResultsPage
import {
    Tabs,
    Tab,
    Card,
    CardContent,
    Button,
    Typography,
    TextField,
    Box,
    Grid,
    MenuItem,
} from "@mui/material";

const tabTypes = [
    "Ramp-Up Test",
    "QPS Test",
    "Spike Test",
    "Soak Test",
    "Stress Test",
    "Data-Driven Test",
];

// Define which fields are relevant for each test type
const tabFieldMap = {
    "Ramp-Up Test": ["users", "spawnRate", "duration", "thinkTimeRange"],
    "QPS Test": ["targetQps", "users", "spawnRate", "duration", "thinkTimeRange"], // 'users' and 'spawnRate' are also needed for Locust CLI
    "Spike Test": ["startUsers", "spikeUsers", "spikeAfter", "duration"],
    "Soak Test": ["users", "duration", "thinkTimeRange"],
    "Stress Test": ["startUsers", "endUsers", "stepUsers", "durationPerStep"],
    "Data-Driven Test": ["users", "spawnRate", "duration"], // Data file upload is separate now
};

const methodOptions = ["GET", "POST", "PUT", "PATCH", "DELETE"];
const payloadTypeOptions = ["json", "form", "text", "binary", "protobuf"]; // Added protobuf

const TestTab = ({ type, onTestStart }) => { // Added onTestStart prop
    // Separate states for different types of form data
    const [requestParams, setRequestParams] = useState({
        method: "GET", url: "", host: "", auth_token: "", headers: "", query_params: ""
    });
    const [payloadType, setPayloadType] = useState("json"); // Default payload type
    const [inlinePayload, setInlinePayload] = useState(""); // For payload text area content
    const [loadParams, setLoadParams] = useState({}); // Dynamic load test fields

    const [dataFile, setDataFile] = useState(null); // For DATA_FILE (data.csv/json)
    const [payloadTemplateFile, setPayloadTemplateFile] = useState(null); // For PAYLOAD_TEMPLATE (payload_template.json)
    const [envVarsFile, setEnvVarsFile] = useState(null); // For general env_vars.json file

    const [responseSummary, setResponseSummary] = useState("");
    const [history, setHistory] = useState([]);

    useEffect(() => {
        const saved = localStorage.getItem("perfTestHistory");
        if (saved) setHistory(JSON.parse(saved));
    }, []);

    // Effect to reset loadParams and clear file inputs when tab changes
    useEffect(() => {
        // Reset loadParams based on the new tab type's fields
        const newLoadParams = {};
        (tabFieldMap[type] || []).forEach(field => {
            newLoadParams[field] = ''; // Initialize with empty string
        });
        setLoadParams(newLoadParams);

        // Clear file inputs on tab change
        setDataFile(null);
        setPayloadTemplateFile(null);
        setEnvVarsFile(null);

        setResponseSummary(""); // Clear previous response
    }, [type]);

    // Generic handler for request detail fields
    const handleRequestChange = (e) => {
        setRequestParams({ ...requestParams, [e.target.name]: e.target.value });
    };

    // Generic handler for dynamic load parameter fields
    const handleLoadChange = (e) => {
        setLoadParams({ ...loadParams, [e.target.name]: e.target.value });
    };

    // Handlers for specific file types
    const handleDataFileChange = (e) => setDataFile(e.target.files[0]);
    const handlePayloadTemplateFileChange = (e) => setPayloadTemplateFile(e.target.files[0]);
    const handleEnvVarsFileChange = (e) => setEnvVarsFile(e.target.files[0]);


    const handleSubmit = async () => {
        if (!requestParams.url || !requestParams.host || !requestParams.method || !payloadType) {
            alert("Method, URL, Host, and Payload Type are required fields.");
            return;
        }

        const formData = new FormData();
        formData.append("load_type", type.toUpperCase().replace(/ /g, '_')); // Consistent naming for backend

        // --- 1. Append Common Request Details ---
        Object.entries(requestParams).forEach(([k, v]) => {
            if (v) formData.append(k, v); // Only append if value is non-empty
        });
        formData.append("payloadType", payloadType); // Always send selected payload type

        // Append inline payload content if applicable
        if (inlinePayload && ["POST", "PUT", "PATCH"].includes(requestParams.method.toUpperCase())) {
            formData.append("inlinePayloadContent", inlinePayload); // This matches @RequestParam in Java
        }

        // --- 2. Append Load Parameters (filtered by current tab type) ---
        // Iterate over loadParams and append non-empty values
        Object.entries(loadParams).forEach(([k, v]) => {
            if (v) { // Only append if value exists
                // Special handling for number inputs that might send empty string when cleared
                if (typeof v === 'number' || (typeof v === 'string' && v.trim() !== '')) {
                    formData.append(k, v);
                }
            }
        });

        // --- 3. Append File Uploads ---
        if (envVarsFile) formData.append("envVarsFile", envVarsFile); // This matches @RequestParam(value = "envVarsFile")
        if (dataFile) formData.append("dataFile", dataFile); // This matches @RequestParam(value = "dataFile")
        if (payloadTemplateFile) formData.append("payloadTemplateFile", payloadTemplateFile); // Matches @RequestParam(value = "payloadTemplateFile")

        // Constructing the new endpoint based on test type
        const typeToPathSegment = {
            "Ramp-Up Test": "ramp-up",
            "QPS Test": "qps",
            "Spike Test": "spike",
            "Soak Test": "soak",
            "Stress Test": "stress",
            "Data-Driven Test": "data-driven"
        };
        const apiPathSegment = typeToPathSegment[type] || "generic"; // Default to "generic" if type not found
        const endpoint = `/perf-service/api/${apiPathSegment}/start`;

        console.log("Selected test type:", type);
        console.log("Target API path segment:", apiPathSegment);
        console.log("Submitting to endpoint:", endpoint);
        // Log form data for debugging
        // for (let [key, value] of formData.entries()) {
        //     console.log(key, value);
        // }

        try {
            const res = await fetch(endpoint, { // Use the dynamically constructed endpoint
                method: "POST",
                body: formData,
            });

            const data = await res.json(); // Expect JSON response from backend
            setResponseSummary(JSON.stringify(data, null, 2)); // Pretty print JSON

            if (res.ok && data.test_id) {
                // console.log("Test started with ID:", data.test_id, "Would navigate to /results/", data.test_id);
                if (onTestStart) {
                    onTestStart(data.test_id); // Call the callback to switch view
                }
            }


            // Store history for recently run tests
            const record = {
                timestamp: new Date().toLocaleString(),
                type,
                params: { ...requestParams, ...loadParams, payloadType, inlinePayload },
            };
            const updatedHistory = [record, ...history.slice(0, 9)];
            setHistory(updatedHistory);
            localStorage.setItem("perfTestHistory", JSON.stringify(updatedHistory));

        } catch (error) {
            console.error("Error submitting test:", error);
            setResponseSummary(`Error: ${error.message || "Failed to connect to backend."}`);
        }
    };

    // Conditional rendering logic for payload fields
    const showPayloadRelatedFields = ["POST", "PUT", "PATCH"].includes((requestParams.method || '').toUpperCase());
    // Show inline payload textbox unless payload type is protobuf AND a data file is already chosen
    const showPayloadTextField = showPayloadRelatedFields && !(payloadType === "protobuf" && dataFile);
    // Show data file upload if payload type is protobuf OR it's a Data-Driven test (which is not a payload type)
    const showDataFileField = showPayloadRelatedFields || type === "Data-Driven Test";
    // Show payload template file upload if payload type is JSON, Form, Text, or Binary (and not protobuf)
    const showPayloadTemplateFileField = showPayloadRelatedFields && ["json", "form", "text", "binary"].includes(payloadType);


    return (
        <Box mt={4}>
            {/* Request Details */}
            <Card sx={{ backgroundColor: "#f3e5f5", mb: 3 }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>
                        Request Details
                    </Typography>
                    <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={3}>
                            <TextField
                                select
                                label="Method"
                                name="method"
                                fullWidth
                                value={requestParams.method || "GET"}
                                onChange={handleRequestChange}
                            >
                                {methodOptions.map((opt) => (
                                    <MenuItem key={opt} value={opt}>
                                        {opt}
                                    </MenuItem>
                                ))}
                            </TextField>
                        </Grid>
                        <Grid item xs={12} sm={6} md={3}>
                            <TextField label="URL" name="url" fullWidth value={requestParams.url || ''} onChange={handleRequestChange} />
                        </Grid>
                        <Grid item xs={12} sm={6} md={3}>
                            <TextField label="Host" name="host" fullWidth value={requestParams.host || ''} onChange={handleRequestChange} />
                        </Grid>
                        <Grid item xs={12} sm={6} md={3}>
                            <TextField label="Auth Token (Optional)" name="auth_token" fullWidth value={requestParams.auth_token || ''} onChange={handleRequestChange} />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField
                                label="Headers (JSON format)"
                                name="headers"
                                fullWidth
                                multiline
                                minRows={2}
                                value={requestParams.headers || ''}
                                onChange={handleRequestChange}
                                placeholder='{"Accept":"application/json", "X-Api-Key":"abc"}'
                            />
                        </Grid>
                        <Grid item xs={12}>
                            <TextField
                                label="Query Params (JSON format)"
                                name="query_params"
                                fullWidth
                                multiline
                                minRows={2}
                                value={requestParams.query_params || ''}
                                onChange={handleRequestChange}
                                placeholder='{"param1": "value1", "param2": "value2"}'
                            />
                        </Grid>

                        {/* Payload Type Selector */}
                        {showPayloadRelatedFields && (
                            <Grid item xs={12} sm={6} md={4}>
                                <TextField
                                    select
                                    label="Payload Type"
                                    name="payloadType"
                                    fullWidth
                                    value={payloadType}
                                    onChange={(e) => setPayloadType(e.target.value)}
                                >
                                    {payloadTypeOptions.map((opt) => (
                                        <MenuItem key={opt} value={opt}>
                                            {opt.toUpperCase()}
                                        </MenuItem>
                                    ))}
                                </TextField>
                            </Grid>
                        )}


                        {/* Inline Payload TextField */}
                        {showPayloadTextField && (
                            <Grid item xs={12} sm={6} md={8}>
                                <TextField
                                    label="Inline Payload Content"
                                    name="inlinePayload"
                                    fullWidth
                                    multiline
                                    minRows={3}
                                    value={inlinePayload || ''}
                                    onChange={(e) => setInlinePayload(e.target.value)}
                                    placeholder='Enter JSON, Text, or Binary String here. For Protobuf, use Data File for values.'
                                />
                            </Grid>
                        )}
                    </Grid>
                </CardContent>
            </Card>

            {/* Load Parameters */}
            <Card sx={{ backgroundColor: "#e3f2fd", mb: 3 }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>
                        Load Parameters ({type})
                    </Typography>
                    <Grid container spacing={2}>
                        {tabFieldMap[type]?.map((field) => (
                            <Grid item xs={12} sm={6} md={3} key={field}>
                                <TextField
                                    label={field.replace(/_/g, " ").replace(/\b\w/g, (l) => l.toUpperCase())}
                                    name={field}
                                    fullWidth
                                    value={loadParams[field] || ''} // Set value from state
                                    onChange={handleLoadChange}
                                    // Use type="number" for fields that are typically numbers
                                    type={['users', 'spawnRate', 'duration', 'targetQps', 'startUsers', 'spikeUsers', 'spikeAfter', 'durationPerStep'].includes(field) ? 'number' : 'text'}
                                />
                            </Grid>
                        ))}
                    </Grid>
                </CardContent>
            </Card>

            {/* File Uploads */}
            <Card sx={{ backgroundColor: "#e8f5e9", mb: 3 }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>
                        Test Artifacts
                    </Typography>
                    <Grid container spacing={2}>
                        {showDataFileField && (
                            <Grid item xs={12} sm={6}>
                                <Button variant="contained" component="label" sx={{ mr: 2 }}>
                                    Upload Data File (CSV/JSON)
                                    <input hidden type="file" name="dataFile" onChange={handleDataFileChange} />
                                </Button>
                                {dataFile && (
                                    <Typography variant="body2" sx={{ mt: 1 }}>
                                        Selected: {dataFile.name}
                                    </Typography>
                                )}
                            </Grid>
                        )}
                        {showPayloadTemplateFileField && (
                            <Grid item xs={12} sm={6}>
                                <Button variant="contained" component="label">
                                    Upload Payload Template File
                                    <input hidden type="file" name="payloadTemplateFile" onChange={handlePayloadTemplateFileChange} />
                                </Button>
                                {payloadTemplateFile && (
                                    <Typography variant="body2" sx={{ mt: 1 }}>
                                        Selected: {payloadTemplateFile.name}
                                    </Typography>
                                )}
                            </Grid>
                        )}
                        <Grid item xs={12} sm={6}>
                            <Button variant="contained" component="label">
                                Upload Environment Variables File (JSON)
                                <input hidden type="file" name="envVarsFile" onChange={handleEnvVarsFileChange} />
                            </Button>
                            {envVarsFile && (
                                <Typography variant="body2" sx={{ mt: 1 }}>
                                    Selected: {envVarsFile.name}
                                </Typography>
                            )}
                        </Grid>
                    </Grid>
                </CardContent>
            </Card>

            {/* Submit Button */}
            <Box display="flex" justifyContent="flex-end" mb={3}>
                <Button variant="contained" color="primary" onClick={handleSubmit}>
                    Run Test
                </Button>
            </Box>

            {/* Response Summary */}
            <Card sx={{ backgroundColor: "#fffde7" }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>
                        Test Run Summary
                    </Typography>
                    <TextField
                        multiline
                        fullWidth
                        minRows={8}
                        value={responseSummary}
                        InputProps={{ readOnly: true }}
                    />
                </CardContent>
            </Card>
        </Box>
    );
};

// Renamed original export to PerformanceTestConfigPage for clarity
function PerformanceTestConfigPage({ onTestStart }) { // Added onTestStart prop
    const [activeTab, setActiveTab] = useState(0);

    return (
        <Box sx={{ maxWidth: "90%", mx: "auto", mt: 4 }}>
            <Typography variant="h4" align="center" gutterBottom>
                Performance Test Designer
            </Typography>
            <Tabs
                value={activeTab}
                onChange={(_, val) => setActiveTab(val)}
                centered
                indicatorColor="primary"
                textColor="primary"
            >
                {tabTypes.map((label, idx) => (
                    <Tab key={idx} label={label} />
                ))}
            </Tabs>
            {/* Pass onTestStart to TestTab */}
            <TestTab type={tabTypes[activeTab]} onTestStart={onTestStart} />
        </Box>
    );
}

// New main App component to handle view switching
export default function App() {
    const [currentView, setCurrentView] = useState("config"); // "config" or "results"
    const [activeTestId, setActiveTestId] = useState(null);

    const handleTestStart = (testId) => {
        setActiveTestId(testId);
        setCurrentView("results");
    };

    const handleBackToConfig = () => {
        setCurrentView("config");
        setActiveTestId(null);
    };

    if (currentView === "results" && activeTestId) {
        return <LiveResultsPage test_id={activeTestId} onBackToConfig={handleBackToConfig} />;
    }

    return <PerformanceTestConfigPage onTestStart={handleTestStart} />;
}