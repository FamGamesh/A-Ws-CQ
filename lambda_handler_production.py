"""
Production Lambda Handler for MCQ Scraper
COMPLETE CHROME BINARY SOLUTION - All issues addressed
"""

import os
import json
import boto3
import logging
import traceback
import subprocess
from mangum import Mangum
from typing import Dict, Any

# Configure logging for Lambda
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class CompleteChromeBinaryLambdaHandler:
    """Complete Chrome Binary Lambda handler - all compatibility issues resolved"""
    
    def __init__(self):
        self.s3_client = None
        self.secrets_client = None
        self.app = None
        self.mangum_handler = None
        self.initialized = False
        self.browser_status = {
            "installed": False,
            "installation_attempted": False,
            "installation_error": None,
            "browser_pool_initialized": False,
            "chrome_version": None,
            "approach": "chrome_binary_complete"
        }
    
    def initialize(self):
        """Initialize Lambda environment with complete Chrome binary setup"""
        if self.initialized:
            return
            
        try:
            logger.info("🚀 Initializing Complete Chrome Binary Lambda Handler...")
            
            # Set Chrome-specific environment variables  
            os.environ['CHROME_BINARY_PATH'] = '/opt/chrome/chrome'
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/chrome'
            os.environ['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '1'
            os.environ['ENVIRONMENT'] = 'lambda'
            os.environ['PYTHONPATH'] = '/opt/python:/var/task'
            os.environ['BROWSER_APPROACH'] = 'chrome_binary'
            
            # Initialize AWS clients
            self.s3_client = boto3.client('s3')
            self.secrets_client = boto3.client('secretsmanager')
            
            # Load secrets
            self._load_secrets()
            
            # CRITICAL: Initialize Chrome binary environment FIRST
            self._initialize_chrome_binary_environment()
            
            # Import and initialize FastAPI app
            from server import app
            self.app = app
            self.mangum_handler = Mangum(app, lifespan="off")
            
            self.initialized = True
            logger.info("✅ Complete Chrome Binary Lambda handler initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Lambda handler: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _initialize_chrome_binary_environment(self):
        """Initialize Chrome binary with all compatibility fixes"""
        try:
            logger.info("🔧 Initializing Chrome binary environment (complete solution)...")
            
            self.browser_status["installation_attempted"] = True
            
            # Verify Chrome binary exists
            chrome_path = '/opt/chrome/chrome'
            
            if not os.path.exists(chrome_path):
                raise Exception(f"Chrome binary not found at {chrome_path}")
            
            # Fix permissions if needed
            if not os.access(chrome_path, os.X_OK):
                import stat
                os.chmod(chrome_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
                logger.info("🔧 Fixed Chrome binary permissions")
            
            # Test Chrome binary with Lambda-compatible arguments
            try:
                cmd = [chrome_path, '--version', '--no-sandbox', '--disable-gpu']
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                
                if result.returncode == 0:
                    chrome_version = result.stdout.strip()
                    logger.info(f"✅ Chrome binary verified: {chrome_version}")
                    self.browser_status["chrome_version"] = chrome_version
                    self.browser_status["installed"] = True
                    self.browser_status["browser_pool_initialized"] = True
                    
                    # Set for application use
                    os.environ['BROWSER_EXECUTABLE_PATH'] = chrome_path
                    
                else:
                    error_output = result.stderr.strip() if result.stderr else "Unknown error"
                    raise Exception(f"Chrome version check failed: {error_output}")
                    
            except subprocess.TimeoutExpired:
                raise Exception("Chrome binary test timeout")
            except Exception as e:
                raise Exception(f"Chrome binary test failed: {e}")
            
            logger.info("✅ Chrome binary environment initialized successfully")
                
        except Exception as e:
            error_msg = f"Chrome binary initialization failed: {e}"
            logger.error(f"❌ {error_msg}")
            self.browser_status["installation_error"] = error_msg
            self.browser_status["installed"] = False
            self.browser_status["browser_pool_initialized"] = False
            
            # Continue execution - app can handle degraded mode
            logger.warning("⚠️ Continuing in degraded mode without Chrome binary")
    
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
    
    def handle_request(self, event: Dict[str, Any], context: Any) -> Dict[str, Any]:
        """Handle Lambda request with complete error handling"""
        try:
            # Ensure initialization
            if not self.initialized:
                self.initialize()
            
            # Enhanced request parsing for different event formats
            method = self._extract_method(event)
            path = self._extract_path(event)
            
            logger.info(f"📥 Processing: {method} {path}")
            logger.info(f"📋 Event type: {self._detect_event_type(event)}")
            
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
            logger.error(f"📋 Event structure: {json.dumps(event, default=str)[:500]}...")
            logger.error(traceback.format_exc())
            
            return self._create_error_response(500, {
                'error': 'Internal server error',
                'message': str(e),
                'type': 'lambda_handler_error',
                'browser_status': self.browser_status,
                'event_type': self._detect_event_type(event)
            })
    
    def _extract_method(self, event: Dict[str, Any]) -> str:
        """Extract HTTP method from various event formats"""
        # API Gateway REST API format
        if 'httpMethod' in event:
            return event['httpMethod']
        
        # API Gateway HTTP API format
        if 'requestContext' in event and 'http' in event['requestContext']:
            return event['requestContext']['http'].get('method', 'UNKNOWN')
        
        # Lambda Function URL format
        if 'requestContext' in event and 'httpMethod' in event['requestContext']:
            return event['requestContext']['httpMethod']
        
        return 'UNKNOWN'
    
    def _extract_path(self, event: Dict[str, Any]) -> str:
        """Extract path from various event formats"""
        # Direct path
        if 'path' in event:
            return event['path']
        
        # Raw path (HTTP API)
        if 'rawPath' in event:
            return event['rawPath']
        
        # Request context path
        if 'requestContext' in event:
            if 'http' in event['requestContext'] and 'path' in event['requestContext']['http']:
                return event['requestContext']['http']['path']
            if 'path' in event['requestContext']:
                return event['requestContext']['path']
        
        return '/'
    
    def _detect_event_type(self, event: Dict[str, Any]) -> str:
        """Detect the type of Lambda event"""
        if 'httpMethod' in event and 'resource' in event:
            return 'API_Gateway_REST'
        elif 'requestContext' in event and 'http' in event['requestContext']:
            return 'API_Gateway_HTTP'
        elif 'requestContext' in event and 'routeKey' in event['requestContext']:
            return 'Lambda_Function_URL'
        else:
            return 'Unknown'
    
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
complete_chrome_handler = CompleteChromeBinaryLambdaHandler()

def handler(event, context):
    """
    Complete Chrome Binary Lambda entry point
    This is the function that AWS Lambda will call
    """
    return complete_chrome_handler.handle_request(event, context)

# For backward compatibility
lambda_handler = handler