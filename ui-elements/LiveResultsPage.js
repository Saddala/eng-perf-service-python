import React, { useState, useEffect, useCallback } from 'react';
import {
    Box, Typography, Card, CardContent, Grid, Table, TableBody,
    TableCell, TableContainer, TableHead, TableRow, Paper,
    CircularProgress, Alert, Button
} from '@mui/material';

// Helper to format numbers
const formatNumber = (num, digits = 2) => {
    if (num === null || num === undefined) return 'N/A';
    return Number(num).toFixed(digits);
};

// Helper to format percentile keys (e.g., "Total" or "95.0")
const formatPercentileKey = (key) => {
    if (key === "Total") return "Total";
    const num = parseFloat(key);
    if (!isNaN(num)) {
        return `${num.toFixed(1)}%`;
    }
    return key;
};


const LiveResultsPage = ({ test_id, onBackToConfig }) => {
    const [stats, setStats] = useState(null);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    const [isTestComplete, setIsTestComplete] = useState(false);

    const fetchStats = useCallback(async () => {
        if (!test_id) {
            setError("No Test ID provided.");
            setLoading(false);
            return;
        }
        // console.log(`Fetching stats for ${test_id}`);
        try {
            const response = await fetch(`/perf-service/api/results/${test_id}/live`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `Error fetching results: ${response.statusText}`);
            }
            const data = await response.json();
            setStats(data);
            setError(''); // Clear previous errors

            // Check if the test is complete
            if (data.state && (data.state.toLowerCase() === 'stopped' || data.state.toLowerCase() === 'finished' || data.state.toLowerCase() === 'cleanup')) {
                setIsTestComplete(true);
                console.log(`Test ${test_id} reported as complete. State: ${data.state}`);
            }

        } catch (err) {
            console.error("Error fetching live stats:", err);
            setError(err.message);
            // Keep existing stats if a fetch fails, unless it's a 404 for the test itself
            if (err.message.includes("Test ID not found")) {
                setStats(null); // Clear stats if test ID is invalid
            }
        } finally {
            setLoading(false);
        }
    }, [test_id]);

    useEffect(() => {
        setLoading(true); // Set loading true on initial mount or test_id change
        setIsTestComplete(false); // Reset completion state
        setStats(null); // Clear old stats
        setError(''); // Clear old errors

        fetchStats(); // Initial fetch

        if (test_id && !isTestComplete) {
            const intervalId = setInterval(fetchStats, 5000); // Poll every 5 seconds
            return () => clearInterval(intervalId); // Cleanup interval on unmount or if test completes
        }
    }, [test_id, fetchStats, isTestComplete]); // Rerun effect if test_id changes or test completes

    if (!test_id) {
        return <Alert severity="warning">No Test ID specified.</Alert>;
    }

    if (loading && !stats) {
        return (
            <Box display="flex" justifyContent="center" alignItems="center" minHeight="80vh">
                <CircularProgress />
                <Typography variant="h6" sx={{ ml: 2 }}>Loading live results for Test ID: {test_id}...</Typography>
            </Box>
        );
    }

    if (error && !stats) { // Show critical error if no stats could be loaded at all
        return <Alert severity="error">Error loading results: {error}</Alert>;
    }

    if (!stats) {
        return (
            <Box textAlign="center" mt={5}>
                <Typography variant="h5">Waiting for test data for Test ID: {test_id}...</Typography>
                {error && <Alert severity="warning" sx={{ mt: 2 }}>{error}</Alert>}
                 <Button variant="contained" onClick={onBackToConfig} sx={{ mt: 3 }}>
                    Back to Test Configuration
                </Button>
            </Box>
        );
    }

    // Main content rendering when stats are available
    const {
        user_count = 0, current_rps = 0, current_fail_per_sec = 0,
        total_requests = 0, total_failures = 0, state = 'N/A',
        response_times_avg = {}, response_times_percentiles = {},
        errors: testErrors = [] // Renamed to avoid conflict with state 'error'
    } = stats;


    const requestStatsArray = [];
    if (stats.stats && Array.isArray(stats.stats)) { // From newer locust versions potentially
        stats.stats.forEach(s => {
            requestStatsArray.push({
                method: s.method,
                name: s.name,
                num_requests: s.num_requests,
                num_failures: s.num_failures,
                avg_response_time: s.avg_response_time,
                rps: s.current_rps, // Assuming current_rps from individual stat entry
                fail_s: s.current_fail_per_sec, // Assuming current_fail_per_sec from individual stat entry
            });
        });
    } else if (stats.response_times_avg) { // Fallback or different structure
         Object.entries(stats.response_times_avg).forEach(([name, avg_rt]) => {
            // This structure might not have all details like method, rps directly per entry
            // We'd need a more complex mapping if the backend provides richer objects here
            if (name === "Total") return; // Skip total, it's shown in summary
            requestStatsArray.push({
                method: name.split(' ')[0], // Best guess
                name: name.substring(name.indexOf(' ') + 1), // Best guess
                avg_response_time: avg_rt,
                // num_requests, num_failures, rps, fail_s might be missing in this specific structure
            });
        });
    }


    return (
        <Box sx={{ p: 3 }}>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                <Typography variant="h4">Live Test Results</Typography>
                <Button variant="outlined" onClick={onBackToConfig}>
                    Back to Test Configuration
                </Button>
            </Box>
            {error && <Alert severity="warning" sx={{ mb: 2 }}>Update error: {error}</Alert>} {/* Non-critical error during polling */}

            <Card sx={{ mb: 2, backgroundColor: '#e3f2fd' }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>Overall Status</Typography>
                    <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={3}><Typography><b>Test ID:</b> {test_id}</Typography></Grid>
                        <Grid item xs={12} sm={6} md={3}><Typography><b>State:</b> {state || 'N/A'}</Typography></Grid>
                        <Grid item xs={12} sm={6} md={3}><Typography><b>Users:</b> {formatNumber(user_count, 0)}</Typography></Grid>
                         {isTestComplete && <Grid item xs={12} sm={6} md={3}><Alert severity="info" icon={false}>Test is complete.</Alert></Grid>}
                    </Grid>
                </CardContent>
            </Card>

            <Card sx={{ mb: 2, backgroundColor: '#fce4ec' }}>
                <CardContent>
                    <Typography variant="h6" gutterBottom>Key Metrics (Total)</Typography>
                    <Grid container spacing={2}>
                        <Grid item xs={6} sm={3} md={2}><Typography><b>RPS:</b> {formatNumber(current_rps)}</Typography></Grid>
                        <Grid item xs={6} sm={3} md={2}><Typography><b>Failures/sec:</b> {formatNumber(current_fail_per_sec)}</Typography></Grid>
                        <Grid item xs={6} sm={3} md={2}><Typography><b>Requests:</b> {formatNumber(total_requests, 0)}</Typography></Grid>
                        <Grid item xs={6} sm={3} md={2}><Typography><b>Failures:</b> {formatNumber(total_failures, 0)}</Typography></Grid>
                        {stats.stats_total && stats.stats_total.avg_response_time &&
                             <Grid item xs={6} sm={3} md={2}><Typography><b>Avg RT (Total):</b> {formatNumber(stats.stats_total.avg_response_time)} ms</Typography></Grid>
                        }
                    </Grid>
                </CardContent>
            </Card>

            <Typography variant="h5" gutterBottom sx={{ mt: 3 }}>Request Statistics</Typography>
            <TableContainer component={Paper} sx={{ mb: 3 }}>
                <Table sx={{ minWidth: 650 }} aria-label="request statistics table">
                    <TableHead sx={{ backgroundColor: '#f0f0f0' }}>
                        <TableRow>
                            <TableCell>Method</TableCell>
                            <TableCell>Name</TableCell>
                            <TableCell align="right"># Requests</TableCell>
                            <TableCell align="right"># Fails</TableCell>
                            <TableCell align="right">Avg. RT (ms)</TableCell>
                            <TableCell align="right">RPS</TableCell>
                            <TableCell align="right">Failures/s</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {requestStatsArray.map((row, index) => (
                            <TableRow key={index}>
                                <TableCell component="th" scope="row">{row.method || 'N/A'}</TableCell>
                                <TableCell>{row.name}</TableCell>
                                <TableCell align="right">{formatNumber(row.num_requests, 0)}</TableCell>
                                <TableCell align="right">{formatNumber(row.num_failures, 0)}</TableCell>
                                <TableCell align="right">{formatNumber(row.avg_response_time)}</TableCell>
                                <TableCell align="right">{formatNumber(row.rps)}</TableCell>
                                <TableCell align="right">{formatNumber(row.fail_s)}</TableCell>
                            </TableRow>
                        ))}
                         {stats.stats_total && ( // Displaying the "Total" row from stats_total if available
                            <TableRow sx={{ backgroundColor: '#f5f5f5', fontWeight: 'bold' }}>
                                <TableCell colSpan={2}>Total</TableCell>
                                <TableCell align="right">{formatNumber(stats.stats_total.num_requests, 0)}</TableCell>
                                <TableCell align="right">{formatNumber(stats.stats_total.num_failures, 0)}</TableCell>
                                <TableCell align="right">{formatNumber(stats.stats_total.avg_response_time)}</TableCell>
                                <TableCell align="right">{formatNumber(stats.stats_total.total_rps || current_rps)}</TableCell> {/* total_rps or current_rps */}
                                <TableCell align="right">{formatNumber(stats.stats_total.total_fail_per_sec || current_fail_per_sec)}</TableCell>
                            </TableRow>
                        )}
                    </TableBody>
                </Table>
            </TableContainer>

            <Typography variant="h5" gutterBottom sx={{ mt: 3 }}>Response Time Percentiles (ms)</Typography>
            <TableContainer component={Paper} sx={{ mb: 3 }}>
                <Table sx={{ minWidth: 650 }} aria-label="percentiles table">
                    <TableHead sx={{ backgroundColor: '#f0f0f0' }}>
                        <TableRow>
                            <TableCell>Request (Name)</TableCell>
                            {stats.response_times_percentiles && Object.keys(stats.response_times_percentiles["Total"] || {}).map(key => (
                                <TableCell key={key} align="right">{formatPercentileKey(key)}</TableCell>
                            ))}
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {Object.entries(response_times_percentiles || {}).map(([name, percentiles]) => (
                            <TableRow key={name}>
                                <TableCell component="th" scope="row">{name}</TableCell>
                                {Object.values(percentiles || {}).map((value, i) => (
                                    <TableCell key={i} align="right">{formatNumber(value)}</TableCell>
                                ))}
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>

            {testErrors && testErrors.length > 0 && (
                <>
                    <Typography variant="h5" gutterBottom sx={{ mt: 3, color: 'error.main' }}>Errors</Typography>
                    <TableContainer component={Paper}>
                        <Table sx={{ minWidth: 650 }} aria-label="errors table">
                            <TableHead sx={{ backgroundColor: '#f0f0f0' }}>
                                <TableRow>
                                    <TableCell>Request</TableCell>
                                    <TableCell>Method</TableCell>
                                    <TableCell>Error</TableCell>
                                    <TableCell align="right">Occurrences</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {testErrors.map((err, index) => (
                                    <TableRow key={index}>
                                        <TableCell>{err.name}</TableCell>
                                        <TableCell>{err.method}</TableCell>
                                        <TableCell sx={{ color: 'error.main' }}>{err.error}</TableCell>
                                        <TableCell align="right">{formatNumber(err.occurrences, 0)}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>
                </>
            )}
        </Box>
    );
};

export default LiveResultsPage;
