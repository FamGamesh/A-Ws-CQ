"""
Production Lambda Handler for MCQ Scraper
FINAL SOLUTION: HTTP-based scraping (No browser required)
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

class HTTPScrapingLambdaHandler:
    """HTTP-based scraping Lambda handler - Browser-free solution"""
    
    def __init__(self):
        self.s3_client = None
        self.secrets_client = None
        self.app = None
        self.mangum_handler = None
        self.initialized = False
        self.scraping_status = {
            "approach": "http_requests",
            "browser_required": False,
            "http_client_ready": False,
            "beautifulsoup_available": False
        }
    
    def initialize(self):
        """Initialize Lambda environment for HTTP scraping"""
        if self.initialized:
            return
            
        try:
            logger.info("🚀 Initializing HTTP Scraping Lambda Handler...")
            
            # Set HTTP scraping environment variables  
            os.environ['SCRAPING_APPROACH'] = 'http_requests'
            os.environ['ENVIRONMENT'] = 'lambda'
            os.environ['PYTHONPATH'] = '/opt/python:/var/task'
            
            # Initialize AWS clients
            self.s3_client = boto3.client('s3')
            self.secrets_client = boto3.client('secretsmanager')
            
            # Load secrets
            self._load_secrets()
            
            # Verify HTTP scraping capabilities
            self._verify_http_scraping()
            
            # Import and initialize FastAPI app
            from server import app
            self.app = app
            self.mangum_handler = Mangum(app, lifespan="off")
            
            self.initialized = True
            logger.info("✅ HTTP Scraping Lambda handler initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Lambda handler: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _verify_http_scraping(self):
        """Verify HTTP scraping capabilities"""
        try:
            logger.info("🔧 Verifying HTTP scraping capabilities...")
            
            # Test requests library
            try:
                import requests
                self.scraping_status["http_client_ready"] = True
                logger.info(f"✅ Requests library available: {requests.__version__}")
            except ImportError as e:
                logger.error(f"❌ Requests library not available: {e}")
                raise Exception("Requests library required for HTTP scraping")
            
            # Test BeautifulSoup
            try:
                import bs4
                self.scraping_status["beautifulsoup_available"] = True  
                logger.info(f"✅ BeautifulSoup available: {bs4.__version__}")
            except ImportError as e:
                logger.error(f"❌ BeautifulSoup not available: {e}")
                raise Exception("BeautifulSoup required for HTML parsing")
            
            logger.info("✅ HTTP scraping environment verified successfully")
                
        except Exception as e:
            error_msg = f"HTTP scraping verification failed: {e}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
    
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
        """Handle Lambda request with comprehensive error handling"""
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
            
            # Fix event format for Mangum compatibility
            fixed_event = self._fix_event_format(event)
            
            # Process through Mangum
            response = self.mangum_handler(fixed_event, context)
            
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
                'scraping_status': self.scraping_status,
                'event_type': self._detect_event_type(event)
            })
    
    def _fix_event_format(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Fix event format for Mangum compatibility"""
        fixed_event = event.copy()
        
        # Ensure requestContext has all required fields
        if 'requestContext' in fixed_event:
            request_context = fixed_event['requestContext']
            
            # Add missing sourceIp if not present
            if 'http' in request_context and 'sourceIp' not in request_context['http']:
                request_context['http']['sourceIp'] = '127.0.0.1'
            
            # Add missing fields for API Gateway HTTP format
            if 'domainName' not in request_context:
                request_context['domainName'] = 'lambda-url.amazonaws.com'
            
            if 'requestId' not in request_context:
                request_context['requestId'] = 'lambda-test-request'
                
            if 'accountId' not in request_context:
                request_context['accountId'] = '123456789012'
        
        return fixed_event
    
    def _extract_method(self, event: Dict[str, Any]) -> str:
        """Extract HTTP method from various event formats"""
        if 'httpMethod' in event:
            return event['httpMethod']
        
        if 'requestContext' in event and 'http' in event['requestContext']:
            return event['requestContext']['http'].get('method', 'GET')
        
        return 'GET'
    
    def _extract_path(self, event: Dict[str, Any]) -> str:
        """Extract path from various event formats"""
        if 'path' in event:
            return event['path']
        
        if 'rawPath' in event:
            return event['rawPath']
        
        if 'requestContext' in event and 'http' in event['requestContext']:
            return event['requestContext']['http'].get('path', '/')
        
        return '/'
    
    def _detect_event_type(self, event: Dict[str, Any]) -> str:
        """Detect the type of Lambda event"""
        if 'httpMethod' in event and 'resource' in event:
            return 'API_Gateway_REST'
        elif 'requestContext' in event and 'http' in event['requestContext']:
            return 'API_Gateway_HTTP_v2'
        elif 'version' in event and event.get('version') == '2.0':
            return 'Lambda_Function_URL_v2'
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
http_scraping_handler = HTTPScrapingLambdaHandler()

def handler(event, context):
    """
    HTTP Scraping Lambda entry point (Browser-free)
    This is the function that AWS Lambda will call
    """
    return http_scraping_handler.handle_request(event, context)

# For backward compatibility
lambda_handler = handler