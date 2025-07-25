#!/bin/bash
# Test script for local development

echo "🧪 Testing MCQ Scraper locally..."

# Install requirements
pip install -r requirements-dev.txt

# Start server
echo "🚀 Starting FastAPI server..."
uvicorn server:app --host 0.0.0.0 --port 8001 --reload
