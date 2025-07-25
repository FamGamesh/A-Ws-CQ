#!/bin/bash

# API Gateway Testing Script
# Replace YOUR-API-ID with your actual API Gateway ID

API_ID="YOUR-API-ID"
REGION="us-east-1"
STAGE="prod"

BASE_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com/${STAGE}"

echo "🧪 Testing API Gateway Integration"
echo "Base URL: $BASE_URL"
echo "================================="

# Test 1: Health Check
echo "Test 1: Health Check"
echo "URL: $BASE_URL/api/health"
curl -s -w "\nStatus: %{http_code}\n" -H "Content-Type: application/json" "$BASE_URL/api/health"
echo ""

# Test 2: Root Endpoint
echo "Test 2: Root Endpoint"
echo "URL: $BASE_URL/"
curl -s -w "\nStatus: %{http_code}\n" -H "Content-Type: application/json" "$BASE_URL/"
echo ""

# Test 3: API Info
echo "Test 3: API Info"
echo "URL: $BASE_URL"
curl -s -w "\nStatus: %{http_code}\n" -H "Content-Type: application/json" "$BASE_URL"
echo ""

# Test 4: CORS Preflight
echo "Test 4: CORS Preflight"
echo "URL: $BASE_URL/api/health"
curl -s -w "\nStatus: %{http_code}\n" -X OPTIONS \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Content-Type" \
  -H "Origin: https://example.com" \
  "$BASE_URL/api/health"
echo ""

# Test 5: MCQ Scrape Endpoint (POST)
echo "Test 5: MCQ Scrape Endpoint"
echo "URL: $BASE_URL/api/scrape-mcqs"
curl -s -w "\nStatus: %{http_code}\n" -X POST \
  -H "Content-Type: application/json" \
  -d '{"topic": "Mathematics", "exam_type": "SSC", "max_mcqs": 5}' \
  "$BASE_URL/api/scrape-mcqs"
echo ""

echo "================================="
echo "✅ Testing complete!"
echo ""
echo "Expected results:"
echo "- Health Check: Status 200 with JSON response"
echo "- Root Endpoint: Status 200 with API info"
echo "- CORS Preflight: Status 200 with CORS headers"
echo "- MCQ Scrape: Status 200 with job_id"
echo ""
echo "If you see 403 Forbidden errors, follow the API Gateway fix guide."