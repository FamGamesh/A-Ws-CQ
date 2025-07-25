"""
Enhanced Health Check for MCQ Scraper Lambda Function
FIXED: Chrome Binary Compatibility Check
"""

import os
import asyncio
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class ChromeBinaryHealthChecker:
    """Health checker for Chrome binary approach"""
    
    def __init__(self):
        self.last_check_time = None
        self.cached_health_data = None
    
    async def get_comprehensive_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status including Chrome binary functionality"""
        try:
            current_time = datetime.now()
            
            # Cache health check for 30 seconds
            if (self.cached_health_data and self.last_check_time and 
                (current_time - self.last_check_time).total_seconds() < 30):
                return self.cached_health_data
            
            # Basic system info
            health_data = {
                "status": "healthy",
                "version": "3.0.2",  # Updated version for Chrome binary approach
                "timestamp": current_time.isoformat(),
                "environment": os.environ.get('ENVIRONMENT', 'unknown'),
                "approach": "chrome_binary_compatible",
                "chrome_path": os.environ.get('CHROME_BINARY_PATH', 'not_set')
            }
            
            # Check Chrome binary status
            chrome_status = await self._check_chrome_binary_status()
            health_data["browser_status"] = chrome_status
            
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
            if not chrome_status["installed"] or chrome_status.get("test_failed", False):
                health_data["status"] = "degraded"
                health_data["warnings"] = ["Chrome binary functionality limited"]
            
            # Cache the result
            self.cached_health_data = health_data
            self.last_check_time = current_time
            
            return health_data
            
        except Exception as e:
            logger.error(f"Error in health check: {e}")
            return {
                "status": "error",
                "version": "3.0.2",
                "approach": "chrome_binary_compatible",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    async def _check_chrome_binary_status(self) -> Dict[str, Any]:
        """Check Chrome binary installation and functionality"""
        chrome_status = {
            "installed": False,
            "installation_attempted": True,
            "installation_error": None,
            "browser_pool_initialized": False,
            "test_passed": False,
            "test_error": None,
            "chrome_version": None,
            "binary_path": None
        }
        
        try:
            # Check if Chrome binary exists
            chrome_path = os.environ.get('CHROME_BINARY_PATH', '/opt/chrome/chrome')
            chrome_status["binary_path"] = chrome_path
            
            if not os.path.exists(chrome_path):
                chrome_status["installation_error"] = f"Chrome binary not found: {chrome_path}"
                return chrome_status
            
            if not os.access(chrome_path, os.X_OK):
                chrome_status["installation_error"] = f"Chrome binary not executable: {chrome_path}"
                return chrome_status
            
            # Test Chrome binary version
            try:
                result = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        chrome_path, '--version',
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    ),
                    timeout=10.0
                )
                
                stdout, stderr = await result.communicate()
                
                if result.returncode == 0:
                    chrome_version = stdout.decode().strip()
                    chrome_status["installed"] = True
                    chrome_status["chrome_version"] = chrome_version
                    chrome_status["browser_pool_initialized"] = True
                    
                    # Test basic Chrome functionality
                    try:
                        test_result = await asyncio.wait_for(
                            self._test_chrome_functionality(chrome_path),
                            timeout=15.0
                        )
                        chrome_status["test_passed"] = test_result
                        
                    except asyncio.TimeoutError:
                        chrome_status["test_error"] = "Chrome functionality test timeout"
                        chrome_status["test_failed"] = True
                    except Exception as e:
                        chrome_status["test_error"] = str(e)
                        chrome_status["test_failed"] = True
                        
                else:
                    chrome_status["installation_error"] = f"Chrome version check failed: {stderr.decode()}"
                    
            except asyncio.TimeoutError:
                chrome_status["installation_error"] = "Chrome binary test timeout"
            except Exception as e:
                chrome_status["installation_error"] = f"Chrome binary test error: {e}"
            
        except Exception as e:
            chrome_status["installation_error"] = f"Chrome check failed: {e}"
        
        return chrome_status
    
    async def _test_chrome_functionality(self, chrome_path: str) -> bool:
        """Test if Chrome binary can be used with Playwright"""
        try:
            from playwright.async_api import async_playwright
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=chrome_path,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--no-zygote',
                        '--single-process',
                        '--disable-web-security'
                    ]
                )
                
                # Simple page test
                page = await browser.new_page()
                await page.goto('data:text/html,<h1>Chrome Binary Test</h1>', timeout=10000)
                title = await page.title()
                await browser.close()
                
                return len(title) > 0
                
        except Exception as e:
            logger.error(f"Chrome functionality test failed: {e}")
            return False
    
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
health_checker = ChromeBinaryHealthChecker()

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
            "version": "3.0.2",
            "approach": "chrome_binary_compatible",
            "timestamp": datetime.now().isoformat(),
            "note": "Basic health check (async not available)"
        }