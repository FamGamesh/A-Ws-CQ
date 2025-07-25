#!/bin/bash

# Frontend Production Deployment to S3
# Deploy React frontend to S3 with CloudFront

set -e

echo "🎨 Frontend S3 Deployment"
echo "========================="

# Configuration - UPDATE THESE WITH YOUR VALUES
FRONTEND_BUCKET="mcq-frontend-YOUR-NAME-2025"  # Replace with your bucket name
API_GATEWAY_URL="https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod"  # Replace with your API Gateway URL
REGION="us-east-1"

echo "📋 Frontend Deployment Configuration:"
echo "   S3 Bucket: $FRONTEND_BUCKET"
echo "   API Gateway URL: $API_GATEWAY_URL"
echo "   Region: $REGION"
echo ""

# Check if frontend directory exists
if [ ! -d "frontend" ]; then
    echo "📥 Cloning frontend repository..."
    git clone https://github.com/FamGamesh/Mq_Frontend.git frontend
else
    echo "📁 Frontend directory exists, pulling latest changes..."
    cd frontend
    git pull origin main
    cd ..
fi

cd frontend

# Install dependencies
echo "📦 Installing frontend dependencies..."
if command -v yarn > /dev/null; then
    yarn install
else
    npm install
fi

# Create production environment file
echo "🔧 Creating production environment configuration..."
cat > .env.production << EOF
# Production Environment Variables for AWS S3/CloudFront
REACT_APP_BACKEND_URL=$API_GATEWAY_URL
GENERATE_SOURCEMAP=false
PUBLIC_URL=https://$FRONTEND_BUCKET.s3.$REGION.amazonaws.com
EOF

echo "✅ Environment configured with API Gateway URL: $API_GATEWAY_URL"

# Build production version
echo ""
echo "🔨 Building production frontend..."
if command -v yarn > /dev/null; then
    yarn build
else
    npm run build
fi

# Check if build was successful
if [ ! -d "build" ]; then
    echo "❌ Build failed - build directory not found"
    exit 1
fi

echo "✅ Frontend build completed successfully"

# Deploy to S3
echo ""
echo "🚀 Deploying frontend to S3..."

# Upload build files
aws s3 sync build/ s3://$FRONTEND_BUCKET --delete --region $REGION

# Set correct content types
echo "📝 Setting correct content types..."

# HTML files
aws s3 cp s3://$FRONTEND_BUCKET/ s3://$FRONTEND_BUCKET/ \
    --recursive \
    --exclude "*" \
    --include "*.html" \
    --content-type "text/html" \
    --metadata-directive REPLACE \
    --region $REGION

# CSS files
aws s3 cp s3://$FRONTEND_BUCKET/ s3://$FRONTEND_BUCKET/ \
    --recursive \
    --exclude "*" \
    --include "*.css" \
    --content-type "text/css" \
    --metadata-directive REPLACE \
    --region $REGION

# JavaScript files
aws s3 cp s3://$FRONTEND_BUCKET/ s3://$FRONTEND_BUCKET/ \
    --recursive \
    --exclude "*" \
    --include "*.js" \
    --content-type "application/javascript" \
    --metadata-directive REPLACE \
    --region $REGION

# JSON files
aws s3 cp s3://$FRONTEND_BUCKET/ s3://$FRONTEND_BUCKET/ \
    --recursive \
    --exclude "*" \
    --include "*.json" \
    --content-type "application/json" \
    --metadata-directive REPLACE \
    --region $REGION

# Get S3 website URL
S3_WEBSITE_URL="http://$FRONTEND_BUCKET.s3-website-$REGION.amazonaws.com"

echo ""
echo "✅ Frontend deployment completed successfully!"
echo ""
echo "🔗 Access your frontend:"
echo "   S3 Website URL: $S3_WEBSITE_URL"
echo ""
echo "🧪 Test your deployment:"
echo "   1. Open: $S3_WEBSITE_URL"
echo "   2. Try submitting an MCQ request"
echo "   3. Check that it connects to your Lambda backend"
echo ""
echo "💡 Optional: Set up CloudFront for HTTPS and better performance"

cd ..