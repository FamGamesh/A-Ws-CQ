"""
FIXED Production Lambda Handler for MCQ Scraper
Enhanced with proper Playwright browser initialization and error handling
"""

import os
import json
import boto3
import logging
import traceback
import asyncio
from mangum import Mangum
from typing import Dict, Any

# Configure logging for Lambda
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EnhancedProductionLambdaHandler:
    """Enhanced Production-ready Lambda handler with fixed Playwright support"""
    
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
            "browser_pool_initialized": False
        }
    
    def initialize(self):
        """Initialize Lambda environment with fixed browser setup"""
        if self.initialized:
            return
            
        try:
            logger.info("🚀 Initializing Enhanced Production Lambda Handler...")
            
            # Set Lambda-specific environment variables  
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/python/pw-browsers'
            os.environ['ENVIRONMENT'] = 'lambda'
            os.environ['PYTHONPATH'] = '/opt/python:/var/task'
            
            # Initialize AWS clients
            self.s3_client = boto3.client('s3')
            self.secrets_client = boto3.client('secretsmanager')
            
            # Load secrets
            self._load_secrets()
            
            # FIXED: Initialize browser environment before importing app
            self._initialize_browser_environment()
            
            # Import and initialize FastAPI app
            from server import app
            self.app = app
            self.mangum_handler = Mangum(app, lifespan="off")
            
            self.initialized = True
            logger.info("✅ Enhanced Production Lambda handler initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Lambda handler: {e}")
            logger.error(traceback.format_exc())
            raise
    
    def _initialize_browser_environment(self):
        """Initialize browser environment for Playwright"""
        try:
            logger.info("🔧 Initializing browser environment...")
            
            self.browser_status["installation_attempted"] = True
            
            # Set up environment for browser detection
            browser_path = '/opt/python/pw-browsers'
            
            if not os.path.exists(browser_path):
                raise Exception(f"Browser directory not found: {browser_path}")
            
            # Look for installed Chromium
            chromium_found = False
            executable_path = None
            
            for item in os.listdir(browser_path):
                if 'chromium' in item.lower():
                    item_path = os.path.join(browser_path, item)
                    if os.path.isdir(item_path):
                        # Try to find the executable
                        possible_executables = [
                            os.path.join(item_path, 'chrome-linux', 'chrome'),
                            os.path.join(item_path, 'chrome-linux', 'headless_shell'),
                            os.path.join(item_path, 'chromium-linux', 'chrome'),
                            os.path.join(item_path, 'chromium'),
                            os.path.join(item_path, 'chrome')
                        ]
                        
                        for exe_path in possible_executables:
                            if os.path.exists(exe_path) and os.access(exe_path, os.X_OK):
                                executable_path = exe_path
                                chromium_found = True
                                logger.info(f"✅ Found Chromium executable: {executable_path}")
                                break
                        
                        if chromium_found:
                            break
            
            if chromium_found:
                # Set browser executable path for the application
                os.environ['BROWSER_EXECUTABLE_PATH'] = executable_path
                self.browser_status["installed"] = True
                self.browser_status["browser_pool_initialized"] = True
                logger.info("✅ Browser environment initialized successfully")
            else:
                raise Exception("No Chromium executable found in browser directory")
                
        except Exception as e:
            error_msg = f"Browser initialization failed: {e}"
            logger.error(f"❌ {error_msg}")
            self.browser_status["installation_error"] = error_msg
            self.browser_status["installed"] = False
            self.browser_status["browser_pool_initialized"] = False
            
            # Continue without browsers - app will handle gracefully
            logger.warning("⚠️ Continuing without browser initialization")
    
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
        """Handle Lambda request with enhanced error handling"""
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
                'type': 'lambda_handler_error',
                'browser_status': self.browser_status
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
enhanced_production_handler = EnhancedProductionLambdaHandler()

def handler(event, context):
    """
    Enhanced Lambda entry point with fixed browser support
    This is the function that AWS Lambda will call
    """
    return enhanced_production_handler.handle_request(event, context)

# For backward compatibility
lambda_handler = handler
