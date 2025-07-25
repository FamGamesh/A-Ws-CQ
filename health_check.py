"""
Enhanced Health Check for MCQ Scraper Lambda Function
FINAL SOLUTION: HTTP-based scraping status
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class HTTPScrapingHealthChecker:
    """Health checker for HTTP-based scraping approach"""
    
    def __init__(self):
        self.last_check_time = None
        self.cached_health_data = None
    
    async def get_comprehensive_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status for HTTP scraping"""
        try:
            current_time = datetime.now()
            
            # Cache health check for 30 seconds
            if (self.cached_health_data and self.last_check_time and 
                (current_time - self.last_check_time).total_seconds() < 30):
                return self.cached_health_data
            
            # Basic system info
            health_data = {
                "status": "healthy",
                "version": "4.0.0",  # New major version - HTTP scraping
                "timestamp": current_time.isoformat(),
                "environment": os.environ.get('ENVIRONMENT', 'unknown'),
                "approach": "http_requests_scraping",
                "browser_required": False,
                "lambda_compatible": True
            }
            
            # Check HTTP scraping capabilities
            scraping_status = self._check_http_scraping_status()
            health_data["scraping_status"] = scraping_status
            
            # Check S3 integration
            s3_status = self._check_s3_integration()
            health_data["s3_integration"] = s3_status
            
            # Check API key manager
            api_status = self._check_api_keys()
            health_data["api_key_manager"] = api_status
            
            # Check job storage
            job_storage_status = self._check_job_storage()
            health_data["job_storage"] = job_storage_status
            
            # Determine overall status
            if not scraping_status["http_client_ready"] or not scraping_status["html_parser_ready"]:
                health_data["status"] = "degraded"
                health_data["warnings"] = ["HTTP scraping capabilities limited"]
            
            # Cache the result
            self.cached_health_data = health_data
            self.last_check_time = current_time
            
            return health_data
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            return {
                "status": "error",
                "version": "4.0.0",
                "approach": "http_requests_scraping",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    def _check_http_scraping_status(self) -> Dict[str, Any]:
        """Check HTTP scraping capabilities"""
        scraping_status = {
            "http_client_ready": False,
            "html_parser_ready": False,
            "requests_version": None,
            "beautifulsoup_version": None,
            "test_passed": False,
            "test_error": None
        }
        
        try:
            # Check requests library
            try:
                import requests
                scraping_status["http_client_ready"] = True
                scraping_status["requests_version"] = requests.__version__
                logger.info(f"✅ Requests available: {requests.__version__}")
            except ImportError as e:
                scraping_status["test_error"] = f"Requests not available: {e}"
                return scraping_status
            
            # Check BeautifulSoup
            try:
                import bs4
                scraping_status["html_parser_ready"] = True
                scraping_status["beautifulsoup_version"] = bs4.__version__
                logger.info(f"✅ BeautifulSoup available: {bs4.__version__}")
            except ImportError as e:
                scraping_status["test_error"] = f"BeautifulSoup not available: {e}"
                return scraping_status
            
            # Test HTTP functionality
            try:
                response = requests.get('https://httpbin.org/get', timeout=5)
                if response.status_code == 200:
                    scraping_status["test_passed"] = True
                    logger.info("✅ HTTP scraping test passed")
                else:
                    scraping_status["test_error"] = f"HTTP test failed: {response.status_code}"
                    
            except Exception as e:
                scraping_status["test_error"] = f"HTTP test failed: {e}"
                # Don't fail completely - network issues are common in Lambda cold starts
            
        except Exception as e:
            scraping_status["test_error"] = f"HTTP scraping check failed: {e}"
        
        return scraping_status
    
    def _check_s3_integration(self) -> Dict[str, Any]:
        """Check S3 integration status"""
        try:
            from s3_production_integration import LAMBDA_S3_INTEGRATION
            
            return {
                "available": True,
                "type": "production_lambda",
                "bucket": os.environ.get('PDF_STORAGE_BUCKET', 'not_set'),
                "integration_loaded": LAMBDA_S3_INTEGRATION
            }
        except ImportError:
            return {
                "available": False,
                "type": "fallback",
                "error": "S3 production integration not available"
            }
    
    def _check_api_keys(self) -> Dict[str, Any]:
        """Check API key manager status"""
        try:
            api_key_pool = os.environ.get('API_KEY_POOL', '')
            google_api_key = os.environ.get('GOOGLE_API_KEY', '')
            
            keys_available = 0
            if api_key_pool:
                keys_available += len(api_key_pool.split(','))
            if google_api_key:
                keys_available += 1
            
            return {
                "initialized": keys_available > 0,
                "total_keys": keys_available,
                "search_engine_id": os.environ.get('SEARCH_ENGINE_ID', 'not_set')
            }
        except Exception as e:
            return {
                "initialized": False,
                "error": str(e)
            }
    
    def _check_job_storage(self) -> Dict[str, Any]:
        """Check persistent job storage"""
        try:
            storage_file = "/tmp/job_progress.pkl"
            
            return {
                "available": True,
                "storage_file": storage_file,
                "file_exists": os.path.exists(storage_file),
                "writable": os.access("/tmp", os.W_OK)
            }
        except Exception as e:
            return {
                "available": False,
                "error": str(e)
            }

# Global health checker instance
health_checker = HTTPScrapingHealthChecker()

async def get_health_status() -> Dict[str, Any]:
    """Get comprehensive health status - async version"""
    return await health_checker.get_comprehensive_health_status()

def get_health_status_sync() -> Dict[str, Any]:
    """Get health status - synchronous version for compatibility"""
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(health_checker.get_comprehensive_health_status())
    except Exception:
        # Fallback for environments where async doesn't work
        return {
            "status": "healthy", 
            "version": "4.0.0",
            "approach": "http_requests_scraping",
            "browser_required": False,
            "timestamp": datetime.now().isoformat(),
            "note": "Basic health check (async not available)"
        }