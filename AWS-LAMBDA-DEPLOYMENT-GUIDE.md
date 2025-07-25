# 🚀 **MCQ Scraper - AWS Lambda Production Deployment Guide**

## **📦 Package Contents**

This production package contains everything needed to deploy your MCQ Scraper to AWS Lambda:

### **Backend Files:**
- `server.py` - Modified FastAPI server with S3 integration
- `lambda_handler_production.py` - Production Lambda handler
- `s3_production_integration.py` - Complete S3 storage management
- `competitive_exam_keywords.py` - Comprehensive keyword dictionary
- `Dockerfile.production` - Optimized Docker configuration
- `requirements-lambda.txt` - Python dependencies

### **Deployment Scripts:**
- `setup-aws-infrastructure.sh` - Creates all AWS resources
- `deploy-production-lambda.sh` - Deploys backend to Lambda
- `deploy-frontend-s3.sh` - Deploys frontend to S3

### **Configuration Files:**
- `.github/workflows/deploy-lambda.yml` - GitHub Actions workflow
- `health_check.py`, `install_playwright.py`, `post_deploy_setup.py` - Utility scripts

---

## **💰 Cost Breakdown (Monthly)**

### **Light Usage (2,000 requests/month):**
- Lambda: $5-8
- S3: $1-2
- API Gateway: $0.50
- Secrets Manager: $2
- ECR: $0.50
- **Total: ~$9-13/month**

### **Medium Usage (10,000 requests/month):**
- Lambda: $25-35
- S3: $2-5
- API Gateway: $2
- Secrets Manager: $2
- ECR: $0.50
- **Total: ~$31-45/month**

**Your $100 budget will cover 2-3 months of medium usage or 7+ months of light usage.**

---

## **🎯 Deployment Methods**

### **Method 1: Automated CLI Deployment (Recommended)**

If you have AWS CLI access:

```bash
# 1. Setup infrastructure
chmod +x setup-aws-infrastructure.sh
./setup-aws-infrastructure.sh

# 2. Deploy backend
chmod +x deploy-production-lambda.sh  
./deploy-production-lambda.sh

# 3. Deploy frontend
chmod +x deploy-frontend-s3.sh
# Edit the script to add your bucket name and API URL
./deploy-frontend-s3.sh
```

### **Method 2: Web Console Deployment (Your Situation)**

Since you don't have CloudShell access, follow these web console steps:

---

## **🌐 WEB CONSOLE DEPLOYMENT - STEP BY STEP**

### **PHASE 1: Create AWS Resources (30 minutes)**

#### **Step 1: Create S3 Buckets**

**1.1 Frontend Bucket:**
- Go to [S3 Console](https://s3.console.aws.amazon.com/)
- Click "Create bucket"
- **Name**: `mcq-frontend-[your-name]-2025`
- **Region**: US East (N. Virginia)
- **Uncheck** "Block all public access"
- **Check** acknowledgment
- Click "Create bucket"

**1.2 Configure Static Hosting:**
- Open your bucket → **Properties** tab
- **Static website hosting** → **Edit** → **Enable**
- **Index document**: `index.html`
- **Error document**: `index.html`
- Save

**1.3 Set Bucket Policy:**
- **Permissions** tab → **Bucket policy** → **Edit**
- Paste (replace `YOUR-BUCKET-NAME`):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadGetObject",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::YOUR-BUCKET-NAME/*"
    }
  ]
}
```

**1.4 PDF Storage Bucket:**
- Repeat steps 1.1-1.3 with name: `mcq-pdfs-[your-name]-2025`

#### **Step 2: Create ECR Repository**

- Go to [ECR Console](https://console.aws.amazon.com/ecr/)
- Click "Create repository"
- **Name**: `mcq-scraper`
- **Image scan**: Enable
- Click "Create"
- **Copy the Repository URI** (save for later)

#### **Step 3: Create IAM Role**

- Go to [IAM Console](https://console.aws.amazon.com/iam/)
- **Roles** → **Create role**
- **Service**: Lambda → **Next**
- **Attach policies**:
  - ✅ `AWSLambdaBasicExecutionRole`
  - ✅ `AmazonS3FullAccess`
  - ✅ `SecretsManagerReadWrite`
- **Role name**: `mcq-scraper-lambda-role`
- Create role

#### **Step 4: Create Secrets**

**4.1 Google API Keys:**
- Go to [Secrets Manager](https://console.aws.amazon.com/secretsmanager/)
- **Store a new secret** → **Other type**
- **Key-value pairs**:
  - `api_key_pool`: `AIzaSyAsoKcq2DMgtRw-L_3inX9Cq-V6-YNOAVg,AIzaSyCb8zhG3NKzsRvpSb7FwgleNMSSLiQyYpY`
  - `google_api_key`: `AIzaSyAsoKcq2DMgtRw-L_3inX9Cq-V6-YNOAVg`
  - `search_engine_id`: `2701a7d64a00d47fd`
- **Secret name**: `mcq-scraper/google-api-keys`
- Store

**4.2 MongoDB URL:**
- **Store a new secret** → **Other type**
- **Key-value pairs**:
  - `mongo_url`: `mongodb+srv://futexi:2Lzmponw0QiMCYw2@cluster0.tb6golz.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0`
  - `db_name`: `mcq_scraper_lambda`
- **Secret name**: `mcq-scraper/mongodb-url`
- Store

#### **Step 5: Create API Gateway**

- Go to [API Gateway Console](https://console.aws.amazon.com/apigateway/)
- **Create API** → **HTTP API** → **Build**
- **API name**: `mcq-scraper-api`
- **CORS**: Enable with `*` for all fields
- **Create**
- **Copy the Invoke URL** (save for later)

---

### **PHASE 2: Build and Deploy Container (45 minutes)**

#### **Step 6: Build Docker Image**

Since you can't use CloudShell, you have these options:

**Option A: Use AWS Cloud9**
- Go to [Cloud9 Console](https://console.aws.amazon.com/cloud9/)
- **Create environment**
- **Name**: `mcq-builder`
- **Instance**: t3.small
- **Create**

In Cloud9 terminal:
```bash
# Install Docker
sudo yum update -y
sudo yum install -y docker git
sudo service docker start
sudo usermod -a -G docker ec2-user

# Create deployment directory
mkdir mcq-deployment
cd mcq-deployment

# Copy all the files from this package to Cloud9
# You can upload them via Cloud9 file manager or use git
```

**Option B: Local Development + Upload**
- Build locally with Docker Desktop
- Push to ECR using AWS CLI

**Option C: GitHub Actions (Automated)**
- Push code to GitHub repository
- Use the provided `.github/workflows/deploy-lambda.yml`
- Configure AWS credentials in GitHub secrets

#### **Step 7: Create Lambda Function**

- Go to [Lambda Console](https://console.aws.amazon.com/lambda/)
- **Create function** → **Container image**
- **Function name**: `mcq-scraper-backend`
- **Container image URI**: Your ECR URI from Step 2
- **Execution role**: `mcq-scraper-lambda-role`
- **Create function**

**Configure Function:**
- **Configuration** → **General configuration**
- **Memory**: 3008 MB
- **Timeout**: 15 minutes (900 seconds)
- **Environment variables**:
  - `PDF_BUCKET_NAME`: Your PDF bucket name
  - `PLAYWRIGHT_BROWSERS_PATH`: `/opt/python/pw-browsers`
  - `ENVIRONMENT`: `lambda`

#### **Step 8: Configure API Gateway Integration**

- Go back to your API Gateway
- **Integrations** → **Create integration**
- **Integration type**: Lambda function
- **Lambda function**: `mcq-scraper-backend`
- **Create**

**Add Routes:**
- **Routes** → **Create route**
- **Method**: `ANY`
- **Resource path**: `/{proxy+}`
- **Integration**: Select your Lambda integration
- **Create**

**Deploy:**
- **Deploy** → **Stage name**: `prod`
- **Deploy**

---

### **PHASE 3: Deploy Frontend (20 minutes)**

#### **Step 9: Prepare Frontend**

**Download Frontend:**
- Download from: https://github.com/FamGamesh/Mq_Frontend/archive/refs/heads/main.zip
- Extract locally

**Configure Environment:**
- Edit `.env.production`:
```
REACT_APP_BACKEND_URL=https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod
GENERATE_SOURCEMAP=false
```

**Build:**
```bash
cd frontend
npm install
npm run build
```

#### **Step 10: Upload to S3**

- Go to your frontend S3 bucket
- **Upload** all files from `build/` folder
- **Permissions**: Grant public read access
- **Upload**

**Set Content Types:**
- Select `.html` files → **Actions** → **Change metadata**
- **Content-Type**: `text/html`
- Repeat for `.css` → `text/css`, `.js` → `application/javascript`

---

## **🧪 Testing Your Deployment**

### **Backend Test:**
```bash
curl https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod/health
```

### **Frontend Test:**
- Open: `http://YOUR-FRONTEND-BUCKET.s3-website-us-east-1.amazonaws.com`
- Try submitting an MCQ request
- Verify PDF generation and download

---

## **🔧 Troubleshooting**

### **Common Issues:**

**1. Lambda Timeout:**
- Increase memory to 3008 MB
- Increase timeout to 15 minutes

**2. CORS Errors:**
- Ensure API Gateway has CORS enabled
- Check Lambda handler returns CORS headers

**3. S3 Access Denied:**
- Verify bucket policies are set correctly
- Check IAM role has S3 permissions

**4. Secrets Access:**
- Ensure IAM role has SecretsManager permissions
- Verify secret names match exactly

**5. Container Build Issues:**
- Check Dockerfile.production syntax
- Ensure all files are in correct locations

---

## **📊 Monitoring and Maintenance**

### **CloudWatch Logs:**
- Monitor Lambda function logs
- Set up alarms for errors

### **Cost Monitoring:**
- Use AWS Cost Explorer
- Set up billing alerts

### **Performance:**
- Monitor Lambda duration
- Optimize memory allocation based on usage

---

## **🔄 Updates and Scaling**

### **Updating Code:**
1. Rebuild Docker image
2. Push to ECR
3. Update Lambda function code

### **Scaling Considerations:**
- Lambda auto-scales
- Monitor concurrent executions
- Consider provisioned concurrency for consistent performance

---

## **📞 Support**

If you encounter issues:
1. Check CloudWatch logs
2. Verify all resources are in `us-east-1`
3. Ensure all environment variables are set
4. Test each component individually

---

**🎉 Your MCQ Scraper is now production-ready on AWS Lambda!**

**Frontend URL**: `http://YOUR-FRONTEND-BUCKET.s3-website-us-east-1.amazonaws.com`  
**Backend API**: `https://YOUR-API-ID.execute-api.us-east-1.amazonaws.com/prod`