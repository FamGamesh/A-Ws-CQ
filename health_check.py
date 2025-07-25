"""
Enhanced Health Check for MCQ Scraper Lambda Function
Provides comprehensive system status including browser availability
"""

import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class EnhancedHealthChecker:
    """Enhanced health checker with browser status monitoring"""
    
    def __init__(self):
        self.last_check_time = None
        self.last_browser_status = None
        self.cached_health_data = None
    
    async def get_comprehensive_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status including browser functionality"""
        try:
            current_time = datetime.now()
            
            # Cache health check for 30 seconds to avoid repeated browser tests
            if (self.cached_health_data and self.last_check_time and 
                (current_time - self.last_check_time).total_seconds() < 30):
                return self.cached_health_data
            
            # Basic system info
            health_data = {
                "status": "healthy",
                "version": "3.0.1",  # Updated version
                "timestamp": current_time.isoformat(),
                "environment": os.environ.get('ENVIRONMENT', 'unknown'),
                "python_path": os.environ.get('PYTHONPATH', 'not_set'),
                "browser_path": os.environ.get('PLAYWRIGHT_BROWSERS_PATH', 'not_set')
            }
            
            # Check browser status
            browser_status = await self._check_browser_status()
            health_data["browser_status"] = browser_status
            
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
            if not browser_status["installed"] or browser_status.get("test_failed", False):
                health_data["status"] = "degraded"
                health_data["warnings"] = ["Browser functionality limited"]
            
            # Cache the result
            self.cached_health_data = health_data
            self.last_check_time = current_time
            
            return health_data
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            return {
                "status": "error",
                "version": "3.0.1",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    async def _check_browser_status(self) -> Dict[str, Any]:
        """Check Playwright browser installation and functionality"""
        browser_status = {
            "installed": False,
            "installation_attempted": False,
            "installation_error": None,
            "browser_pool_initialized": False,
            "test_passed": False,
            "test_error": None,
            "executable_path": None
        }
        
        try:
            # Check if browser directory exists
            browser_path = os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '/opt/python/pw-browsers')
            
            if not os.path.exists(browser_path):
                browser_status["installation_error"] = f"Browser directory not found: {browser_path}"
                return browser_status
            
            # Look for installed browsers
            chromium_found = False
            executable_path = None
            
            for item in os.listdir(browser_path):
                if 'chromium' in item.lower():
                    item_path = os.path.join(browser_path, item)
                    if os.path.isdir(item_path):
                        # Check for executables
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
                                break
                        
                        if chromium_found:
                            break
            
            if chromium_found:
                browser_status["installed"] = True
                browser_status["executable_path"] = executable_path
                browser_status["browser_pool_initialized"] = True
                
                # Test browser functionality (with timeout)
                try:
                    test_result = await asyncio.wait_for(
                        self._test_browser_functionality(executable_path),
                        timeout=10.0
                    )
                    browser_status["test_passed"] = test_result
                    
                except asyncio.TimeoutError:
                    browser_status["test_error"] = "Browser test timeout"
                    browser_status["test_failed"] = True
                except Exception as e:
                    browser_status["test_error"] = str(e)
                    browser_status["test_failed"] = True
            else:
                browser_status["installation_error"] = "No Chromium executable found"
            
        except Exception as e:
            browser_status["installation_error"] = f"Browser check failed: {e}"
        
        return browser_status
    
    async def _test_browser_functionality(self, executable_path: str) -> bool:
        """Test if browser can be launched and used"""
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=executable_path,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu'
                    ]
                )
                
                # Simple page test
                page = await browser.new_page()
                await page.goto('data:text/html,<h1>Health Check</h1>', timeout=5000)
                title = await page.title()
                await browser.close()
                
                return len(title) > 0
                
        except Exception as e:
            logger.error(f"Browser functionality test failed: {e}")
            return False
    
    def _check_s3_integration(self) -> Dict[str, Any]:
        """Check S3 integration status"""
        try:
            # Import S3 integration
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
health_checker = EnhancedHealthChecker()

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
            "version": "3.0.1",
            "timestamp": datetime.now().isoformat(),
            "note": "Basic health check (async not available)"
        }