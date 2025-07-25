"""
Production Lambda Handler for MCQ Scraper
Optimized for AWS Lambda deployment with complete S3 integration
"""

import os
import json
import boto3
import logging
import traceback
from mangum import Mangum
from typing import Dict, Any

# Configure logging for Lambda
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ProductionLambdaHandler:
    """Production-ready Lambda handler with comprehensive error handling"""
    
    def __init__(self):
        self.s3_client = None
        self.secrets_client = None
        self.app = None
        self.mangum_handler = None
        self.initialized = False
    
    def initialize(self):
        """Initialize Lambda environment and AWS services"""
        if self.initialized:
            return
            
        try:
            # Set Lambda-specific environment variables
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/python/pw-browsers'
            os.environ['ENVIRONMENT'] = 'lambda'
            os.environ['PYTHONPATH'] = '/opt/python:/var/task'
            
            # Initialize AWS clients
            self.s3_client = boto3.client('s3')
            self.secrets_client = boto3.client('secretsmanager')
            
            # Load secrets
            self._load_secrets()
            
            # Import and initialize FastAPI app
            from server import app
            self.app = app
            self.mangum_handler = Mangum(app, lifespan="off")
            
            self.initialized = True
            logger.info("✅ Production Lambda handler initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Lambda handler: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _load_secrets(self):
        """Load secrets from AWS Secrets Manager"""
        try:
            # Load Google API keys
            try:
                google_response = self.secrets_client.get_secret_value(
                    SecretId='mcq-scraper/google-api-keys'
                )
                google_secrets = json.loads(google_response['SecretString'])
                
                os.environ['API_KEY_POOL'] = google_secrets.get('api_key_pool', '')
                os.environ['GOOGLE_API_KEY'] = google_secrets.get('google_api_key', '')
                os.environ['SEARCH_ENGINE_ID'] = google_secrets.get('search_engine_id', '2701a7d64a00d47fd')
                
                logger.info("✅ Google API secrets loaded")
            except Exception as e:
                logger.warning(f"⚠️ Could not load Google API secrets: {e}")
                # Set defaults
                os.environ['SEARCH_ENGINE_ID'] = '2701a7d64a00d47fd'
            
            # Load MongoDB credentials
            try:
                mongo_response = self.secrets_client.get_secret_value(
                    SecretId='mcq-scraper/mongodb-url'
                )
                mongo_secrets = json.loads(mongo_response['SecretString'])
                
                os.environ['MONGO_URL'] = mongo_secrets.get('mongo_url', '')
                os.environ['DB_NAME'] = mongo_secrets.get('db_name', 'mcq_scraper_lambda')
                
                logger.info("✅ MongoDB secrets loaded")
            except Exception as e:
                logger.warning(f"⚠️ Could not load MongoDB secrets: {e}")
            
            # Set S3 bucket from environment
            pdf_bucket = os.environ.get('PDF_BUCKET_NAME', 'mcq-scraper-pdfs-default')
            os.environ['PDF_STORAGE_BUCKET'] = pdf_bucket
            
            logger.info("✅ All secrets processing completed")
            
        except Exception as e:
            logger.error(f"❌ Error loading secrets: {e}")
            # Continue with defaults to allow basic functionality
    
    def handle_request(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Handle Lambda request with comprehensive error handling"""
        try:
            # Ensure initialization
            if not self.initialized:
                self.initialize()
            
            # Log request details
            method = event.get('httpMethod', 'UNKNOWN')
            path = event.get('path', '/')
            logger.info(f"📥 Processing: {method} {path}")
            
            # Handle CORS preflight requests
            if method == 'OPTIONS':
                return self._create_cors_response(200, '')
            
            # Process through Mangum
            response = self.mangum_handler(event, context)
            
            # Ensure CORS headers
            if 'headers' not in response:
                response['headers'] = {}
            
            response['headers'].update(self._get_cors_headers())
            
            logger.info(f"📤 Response: {response.get('statusCode', 'UNKNOWN')}")
            return response
            
        except Exception as e:
            logger.error(f"❌ Request handling error: {e}")
            logger.error(traceback.format_exc())
            
            return self._create_error_response(500, {
                'error': 'Internal server error',
                'message': str(e),
                'type': 'lambda_handler_error'
            })
    
    def _get_cors_headers(self) -> Dict[str, str]:
        """Get CORS headers for responses"""
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With',
            'Access-Control-Max-Age': '86400',
        }
    
    def _create_cors_response(self, status_code: int, body: str) -> Dict[str, Any]:
        """Create CORS-enabled response"""
        return {
            'statusCode': status_code,
            'headers': self._get_cors_headers(),
            'body': body
        }
    
    def _create_error_response(self, status_code: int, error_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create error response with CORS headers"""
        return {
            'statusCode': status_code,
            'headers': {
                **self._get_cors_headers(),
                'Content-Type': 'application/json',
            },
            'body': json.dumps(error_data)
        }

# Global handler instance
production_handler = ProductionLambdaHandler()

def handler(event, context):
    """
    Main Lambda entry point
    This is the function that AWS Lambda will call
    """
    return production_handler.handle_request(event, context)

# For backward compatibility
lambda_handler = handler