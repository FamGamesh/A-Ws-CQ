#!/bin/bash

# Complete AWS Infrastructure Setup via CLI
# Creates all required AWS resources for MCQ Scraper

set -e

echo "🏗️  AWS Infrastructure Setup for MCQ Scraper"
echo "============================================="

# Configuration
PROJECT_NAME="mcq-scraper"
REGION="us-east-1"

# Generate unique identifiers
TIMESTAMP=$(date +%s)
FRONTEND_BUCKET="${PROJECT_NAME}-frontend-${TIMESTAMP}"
PDF_BUCKET="${PROJECT_NAME}-pdfs-${TIMESTAMP}"

echo "📋 Infrastructure Configuration:"
echo "   Project: $PROJECT_NAME"
echo "   Region: $REGION"
echo "   Frontend Bucket: $FRONTEND_BUCKET"
echo "   PDF Bucket: $PDF_BUCKET"
echo ""

# Get AWS Account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "🔍 AWS Account ID: $ACCOUNT_ID"

# Create S3 buckets
echo ""
echo "📦 Creating S3 buckets..."

# Frontend bucket
aws s3 mb s3://$FRONTEND_BUCKET --region $REGION
echo "   ✅ Frontend bucket created: $FRONTEND_BUCKET"

# PDF storage bucket
aws s3 mb s3://$PDF_BUCKET --region $REGION
echo "   ✅ PDF bucket created: $PDF_BUCKET"

# Configure frontend bucket for static website hosting
echo "🌐 Configuring static website hosting..."
aws s3 website s3://$FRONTEND_BUCKET \
    --index-document index.html \
    --error-document index.html

# Set bucket policies for public access
echo "🔐 Setting bucket policies..."

# Frontend bucket policy
cat > frontend-bucket-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$FRONTEND_BUCKET/*"
    }
  ]
}
EOF

aws s3api put-bucket-policy --bucket $FRONTEND_BUCKET --policy file://frontend-bucket-policy.json

# PDF bucket policy
cat > pdf-bucket-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$PDF_BUCKET/*"
    }
  ]
}
EOF

aws s3api put-bucket-policy --bucket $PDF_BUCKET --policy file://pdf-bucket-policy.json

# Create ECR repository
echo ""
echo "🐳 Creating ECR repository..."
aws ecr create-repository --repository-name $PROJECT_NAME --region $REGION
ECR_URI="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/$PROJECT_NAME"
echo "   ✅ ECR repository created: $ECR_URI"

# Create IAM role for Lambda
echo ""
echo "🔐 Creating IAM role for Lambda..."

# Trust policy
cat > lambda-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
    --role-name ${PROJECT_NAME}-lambda-role \
    --assume-role-policy-document file://lambda-trust-policy.json

# Custom policy for Lambda
cat > lambda-custom-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:*:*:*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::$PDF_BUCKET",
        "arn:aws:s3:::$PDF_BUCKET/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "*"
    }
  ]
}
EOF

aws iam create-policy \
    --policy-name ${PROJECT_NAME}-lambda-policy \
    --policy-document file://lambda-custom-policy.json

# Attach policies to role
aws iam attach-role-policy \
    --role-name ${PROJECT_NAME}-lambda-role \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

CUSTOM_POLICY_ARN="arn:aws:iam::$ACCOUNT_ID:policy/${PROJECT_NAME}-lambda-policy"
aws iam attach-role-policy \
    --role-name ${PROJECT_NAME}-lambda-role \
    --policy-arn $CUSTOM_POLICY_ARN

LAMBDA_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/${PROJECT_NAME}-lambda-role"
echo "   ✅ Lambda role created: $LAMBDA_ROLE_ARN"

# Create Secrets Manager secrets
echo ""
echo "🔑 Creating secrets in AWS Secrets Manager..."

# Google API keys secret
aws secretsmanager create-secret \
    --name "${PROJECT_NAME}/google-api-keys" \
    --description "Google API keys for MCQ scraper" \
    --secret-string '{
        "api_key_pool": "AIzaSyAsoKcq2DMgtRw-L_3inX9Cq-V6-YNOAVg,AIzaSyCb8zhG3NKzsRvpSb7FwgleNMSSLiQyYpY",
        "google_api_key": "AIzaSyAsoKcq2DMgtRw-L_3inX9Cq-V6-YNOAVg",
        "search_engine_id": "2701a7d64a00d47fd"
    }'

# MongoDB secret
aws secretsmanager create-secret \
    --name "${PROJECT_NAME}/mongodb-url" \
    --description "MongoDB connection details" \
    --secret-string '{
        "mongo_url": "mongodb+srv://futexi:2Lzmponw0QiMCYw2@cluster0.tb6golz.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0",
        "db_name": "mcq_scraper_lambda"
    }'

echo "   ✅ Secrets created successfully"

# Create API Gateway
echo ""
echo "🌐 Creating API Gateway..."

API_ID=$(aws apigatewayv2 create-api \
    --name ${PROJECT_NAME}-api \
    --protocol-type HTTP \
    --cors-configuration AllowOrigins="*",AllowMethods="*",AllowHeaders="*" \
    --region $REGION \
    --query 'ApiId' \
    --output text)

API_URL="https://$API_ID.execute-api.$REGION.amazonaws.com/prod"
echo "   ✅ API Gateway created: $API_URL"

# Save configuration
echo ""
echo "💾 Saving configuration..."

cat > aws-resources.txt << EOF
# AWS Resources for MCQ Scraper
FRONTEND_BUCKET=$FRONTEND_BUCKET
PDF_BUCKET=$PDF_BUCKET
ECR_URI=$ECR_URI
LAMBDA_ROLE_ARN=$LAMBDA_ROLE_ARN
API_ID=$API_ID
API_URL=$API_URL
REGION=$REGION
PROJECT_NAME=$PROJECT_NAME
ACCOUNT_ID=$ACCOUNT_ID
EOF

# Clean up temporary files
rm -f lambda-trust-policy.json lambda-custom-policy.json
rm -f frontend-bucket-policy.json pdf-bucket-policy.json

echo ""
echo "✅ AWS Infrastructure setup completed successfully!"
echo ""
echo "📋 Resource Summary:"
echo "   Frontend Bucket: $FRONTEND_BUCKET"
echo "   PDF Bucket: $PDF_BUCKET"
echo "   ECR Repository: $ECR_URI"
echo "   Lambda Role: $LAMBDA_ROLE_ARN"
echo "   API Gateway: $API_URL"
echo ""
echo "📁 Configuration saved to: aws-resources.txt"
echo ""
echo "🔄 Next Steps:"
echo "   1. Create Lambda function (manually via console or run deploy script)"
echo "   2. Build and push Docker image to ECR"
echo "   3. Configure API Gateway integration"
echo "   4. Deploy frontend to S3"
echo "   5. Test complete application"