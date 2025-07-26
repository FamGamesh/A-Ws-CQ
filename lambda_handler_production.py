"""
Production Lambda Handler for MCQ Scraper - CORS Fixed Version
FINAL SOLUTION: HTTP-based scraping with proper CORS configuration
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
    """HTTP-based scraping Lambda handler with fixed CORS configuration"""
    
    def __init__(self):
        self.s3_client = None
        self.secrets_client = None
        self.lambda_client = None
        self.app = None
        self.mangum_handler = None
        self.initialized = False
        self.scraping_status = {
            "approach": "http_requests",
            "browser_required": False,
            "http_client_ready": False,
            "beautifulsoup_available": False,
            "api_gateway_ready": False,
            "cors_fixed": True
        }
    
    def initialize(self):
        """Initialize Lambda environment for HTTP scraping with API Gateway integration"""
        if self.initialized:
            return
            
        try:
            logger.info("🚀 Initializing HTTP Scraping Lambda Handler with CORS fixes...")
            
            # Set HTTP scraping environment variables  
            os.environ['SCRAPING_APPROACH'] = 'http_requests'
            os.environ['ENVIRONMENT'] = 'lambda'
            os.environ['PYTHONPATH'] = '/opt/python:/var/task'
            
            # Initialize AWS clients
            self.s3_client = boto3.client('s3')
            self.secrets_client = boto3.client('secretsmanager')
            self.lambda_client = boto3.client('lambda')
            
            # Load secrets
            self._load_secrets()
            
            # Verify HTTP scraping capabilities
            self._verify_http_scraping()
            
            # Setup API Gateway resource policy
            self._setup_api_gateway_permissions()
            
            # Import and initialize FastAPI app
            from server import app
            self.app = app
            
            # Initialize Mangum with optimized settings for API Gateway
            self.mangum_handler = Mangum(
                app, 
                lifespan="off",
                api_gateway_base_path="/prod",  # Match your API Gateway stage
                text_mime_types=[
                    "application/json",
                    "application/javascript",
                    "application/xml",
                    "application/vnd.api+json",
                    "application/x-www-form-urlencoded",
                    "text/csv",
                    "text/html",
                    "text/plain",
                    "text/xml"
                ]
            )
            
            self.initialized = True
            self.scraping_status["api_gateway_ready"] = True
            logger.info("✅ HTTP Scraping Lambda handler initialized successfully with CORS fixes")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Lambda handler: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _setup_api_gateway_permissions(self):
        """Setup permissions for API Gateway to invoke Lambda"""
        try:
            # Get current function name
            function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'mcq-scraper-backend')
            
            try:
                self.lambda_client.add_permission(
                    FunctionName=function_name,
                    StatementId="AllowAPIGatewayInvoke",
                    Action="lambda:InvokeFunction",
                    Principal="apigateway.amazonaws.com",
                    SourceArn=f"arn:aws:execute-api:{os.environ.get('AWS_REGION', 'us-east-1')}:*:*/*/*"
                )
                logger.info("✅ API Gateway permissions configured")
            except Exception as e:
                if "ResourceConflictException" in str(e):
                    logger.info("ℹ️ API Gateway permissions already exist")
                else:
                    logger.warning(f"⚠️ Could not set API Gateway permissions: {e}")
                    
        except Exception as e:
            logger.warning(f"⚠️ Error setting up API Gateway permissions: {e}")
    
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
        """Handle Lambda request with comprehensive error handling and fixed CORS"""
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
                return self._create_cors_response(200, json.dumps({"message": "CORS preflight successful"}))
            
            # Handle root path requests
            if path == '/' or path == '':
                return self._create_response(200, {
                    "message": "MCQ Scraper API - CORS Fixed",
                    "version": "4.0.1",
                    "approach": "http_requests_scraping",
                    "browser_required": False,
                    "cors_fixed": True,
                    "status": "running",
                    "available_endpoints": [
                        "GET /api/health",
                        "POST /api/scrape-mcqs",
                        "GET /api/job-status/{job_id}"
                    ]
                })
            
            # Fix event format for Mangum compatibility
            fixed_event = self._fix_event_format(event)
            
            # Process through Mangum
            response = self.mangum_handler(fixed_event, context)
            
            # Ensure CORS headers are present and consistent
            if 'headers' not in response:
                response['headers'] = {}
            
            response['headers'].update(self._get_cors_headers())
            
            # Ensure proper content type
            if 'Content-Type' not in response['headers']:
                response['headers']['Content-Type'] = 'application/json'
            
            logger.info(f"📤 Response: {response.get('statusCode', 'UNKNOWN')}")
            return response
            
        except Exception as e:
            logger.error(f"❌ Request handling error: {e}")
            logger.error(f"📋 Event structure: {json.dumps(event, default=str)[:1000]}...")
            logger.error(traceback.format_exc())
            
            return self._create_error_response(500, {
                'error': 'Internal server error',
                'message': str(e),
                'type': 'lambda_handler_error',
                'cors_fixed': True,
                'scraping_status': self.scraping_status,
                'event_type': self._detect_event_type(event),
                'path': self._extract_path(event),
                'method': self._extract_method(event)
            })
    
    def _fix_event_format(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Fix event format for Mangum compatibility"""
        fixed_event = event.copy()
        
        # Ensure requestContext has all required fields
        if 'requestContext' not in fixed_event:
            fixed_event['requestContext'] = {}
        
        request_context = fixed_event['requestContext']
        
        # Handle different API Gateway event formats
        if 'http' in request_context:
            # HTTP API v2.0 format
            if 'sourceIp' not in request_context['http']:
                request_context['http']['sourceIp'] = '127.0.0.1'
        else:
            # REST API format - add http context
            request_context['http'] = {
                'method': self._extract_method(event),
                'path': self._extract_path(event),
                'sourceIp': '127.0.0.1'
            }
        
        # Add missing fields for API Gateway compatibility
        if 'domainName' not in request_context:
            request_context['domainName'] = 'lambda-url.amazonaws.com'
        
        if 'requestId' not in request_context:
            request_context['requestId'] = 'lambda-request-' + str(hash(str(event)))[:8]
            
        if 'accountId' not in request_context:
            request_context['accountId'] = '123456789012'
        
        if 'stage' not in request_context:
            request_context['stage'] = 'prod'
        
        # Ensure headers exist
        if 'headers' not in fixed_event:
            fixed_event['headers'] = {}
        
        # Ensure multiValueHeaders exist for REST API
        if 'multiValueHeaders' not in fixed_event:
            fixed_event['multiValueHeaders'] = {}
        
        # Ensure pathParameters exist
        if 'pathParameters' not in fixed_event:
            fixed_event['pathParameters'] = {}
        
        # Ensure queryStringParameters exist
        if 'queryStringParameters' not in fixed_event:
            fixed_event['queryStringParameters'] = {}
        
        # CRITICAL FIX: Fix Host header for mangum compatibility
        # The Host header should not contain protocol or path, only the domain
        if 'headers' in fixed_event and 'Host' in fixed_event['headers']:
            host_value = fixed_event['headers']['Host']
            
            # Remove protocol if present
            if host_value.startswith('https://'):
                host_value = host_value[8:]
            elif host_value.startswith('http://'):
                host_value = host_value[7:]
            
            # Remove path if present (everything after the first '/')
            if '/' in host_value:
                host_value = host_value.split('/')[0]
            
            # Update the Host header with the clean domain name
            fixed_event['headers']['Host'] = host_value
            
            logger.info(f"🔧 Fixed Host header: {fixed_event['headers']['Host']}")
        
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
        """Get CORS headers for responses - FIXED VERSION"""
        return {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Requested-With, Accept, Origin, X-Amz-Date, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent',
            'Access-Control-Allow-Credentials': 'false',  # CRITICAL FIX: Must be 'false' when using '*' for origin
            'Access-Control-Max-Age': '86400',
            'Access-Control-Expose-Headers': 'Content-Type, Authorization, X-Requested-With'
        }
    
    def _create_cors_response(self, status_code: int, body: str) -> Dict[str, Any]:
        """Create CORS-enabled response"""
        return {
            'statusCode': status_code,
            'headers': {
                **self._get_cors_headers(),
                'Content-Type': 'application/json'
            },
            'body': body
        }
    
    def _create_response(self, status_code: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create standard response with CORS headers"""
        return {
            'statusCode': status_code,
            'headers': {
                **self._get_cors_headers(),
                'Content-Type': 'application/json'
            },
            'body': json.dumps(data)
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
    HTTP Scraping Lambda entry point with CORS fixes
    This is the function that AWS Lambda will call
    """
    return http_scraping_handler.handle_request(event, context)

# For backward compatibility
lambda_handler = handler