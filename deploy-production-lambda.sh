#!/bin/bash

# Production AWS Lambda Deployment Script
# Deploy MCQ Scraper to AWS Lambda using web console resources

set -e

echo "🚀 Production AWS Lambda Deployment"
echo "====================================="

# Configuration
PROJECT_NAME="mcq-scraper"
REGION="us-east-1"
ECR_REPOSITORY="mcq-scraper"
LAMBDA_FUNCTION="mcq-scraper-backend"

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "❌ AWS CLI not configured. Please run 'aws configure' first."
    exit 1
fi

# Get account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$ECR_REPOSITORY"

echo "📋 Deployment Configuration:"
echo "   Account ID: $ACCOUNT_ID"
echo "   Region: $REGION"
echo "   ECR URI: $ECR_URI"
echo "   Lambda Function: $LAMBDA_FUNCTION"
echo ""

# Check if required files exist
required_files=(
    "Dockerfile.production"
    "lambda_handler_production.py"
    "s3_production_integration.py"
    "server.py"
    "competitive_exam_keywords.py"
    "requirements-lambda.txt"
)

echo "🔍 Checking required files..."
for file in "${required_files[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Missing required file: $file"
        exit 1
    fi
    echo "   ✅ $file"
done

# Login to ECR
echo ""
echo "🔐 Logging into Amazon ECR..."
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_URI

# Build Docker image
echo ""
echo "🔨 Building Docker image..."
docker build -f Dockerfile.production -t $PROJECT_NAME:latest .

# Tag for ECR
echo "🏷️  Tagging image for ECR..."
docker tag $PROJECT_NAME:latest $ECR_URI:latest

# Push to ECR
echo ""
echo "📤 Pushing image to ECR..."
docker push $ECR_URI:latest

# Update Lambda function
echo ""
echo "⚡ Updating Lambda function..."
aws lambda update-function-code \
    --function-name $LAMBDA_FUNCTION \
    --image-uri $ECR_URI:latest \
    --region $REGION

# Wait for update to complete
echo "⏳ Waiting for Lambda function update to complete..."
aws lambda wait function-updated --function-name $LAMBDA_FUNCTION --region $REGION

# Update function configuration
echo "🔧 Updating Lambda function configuration..."
aws lambda update-function-configuration \
    --function-name $LAMBDA_FUNCTION \
    --timeout 900 \
    --memory-size 3008 \
    --region $REGION

# Test the function
echo ""
echo "🧪 Testing Lambda function..."
TEST_PAYLOAD='{"httpMethod":"GET","path":"/health","headers":{},"body":null}'
aws lambda invoke \
    --function-name $LAMBDA_FUNCTION \
    --payload "$TEST_PAYLOAD" \
    --region $REGION \
    response.json

if [ -f "response.json" ]; then
    echo "📋 Lambda response:"
    cat response.json | jq .
    rm response.json
fi

echo ""
echo "✅ Production deployment completed successfully!"
echo ""
echo "🔗 Next steps:"
echo "   1. Test your API Gateway endpoint"
echo "   2. Deploy frontend to S3"
echo "   3. Update frontend with API Gateway URL"
echo "   4. Test complete application flow"