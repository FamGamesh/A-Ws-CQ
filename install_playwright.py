#!/usr/bin/env python3
"""
FIXED Playwright Installation Script for AWS Lambda Environment
Addresses GLIBC compatibility and path resolution issues
"""

import os
import subprocess
import sys
import logging
from pathlib import Path
import asyncio

# Configure logging for Lambda environment
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def setup_lambda_environment():
    """Setup Lambda-specific environment variables"""
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/python/pw-browsers'
    os.environ['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '0'
    os.environ['PYTHONPATH'] = '/opt/python:/var/task'
    
    # Ensure directories exist
    os.makedirs('/opt/python/pw-browsers', exist_ok=True)
    os.makedirs('/tmp/pdfs', exist_ok=True)
    
    logger.info("Lambda environment setup completed")

def verify_browser_installation():
    """Verify that browsers are properly installed"""
    try:
        browser_path = '/opt/python/pw-browsers'
        
        if not os.path.exists(browser_path):
            logger.error("Browser directory doesn't exist")
            return False
        
        # Look for Chromium installation
        chromium_found = False
        for item in os.listdir(browser_path):
            if 'chromium' in item.lower():
                chromium_path = os.path.join(browser_path, item)
                logger.info(f"Found Chromium installation: {chromium_path}")
                
                # Look for executable
                possible_executables = [
                    os.path.join(chromium_path, 'chrome-linux', 'chrome'),
                    os.path.join(chromium_path, 'chrome-linux', 'headless_shell'),
                    os.path.join(chromium_path, 'chromium-linux', 'chrome'),
                    os.path.join(chromium_path, 'chromium'),
                    os.path.join(chromium_path, 'chrome')
                ]
                
                for exe in possible_executables:
                    if os.path.exists(exe):
                        logger.info(f"Found executable: {exe}")
                        if os.access(exe, os.X_OK):
                            chromium_found = True
                            # Set environment variable for the found executable
                            os.environ['BROWSER_EXECUTABLE_PATH'] = exe
                            break
                
                if chromium_found:
                    break
        
        return chromium_found
        
    except Exception as e:
        logger.error(f"Error verifying browser installation: {e}")
        return False

async def test_playwright_functionality():
    """Test that Playwright can actually launch and use browsers"""
    try:
        from playwright.async_api import async_playwright
        
        logger.info("Testing Playwright functionality...")
        
        async with async_playwright() as p:
            # Try to launch with explicit executable path if available
            launch_args = {
                'headless': True,
                'args': [
                    '--no-sandbox',
                    '--disable-setuid-sandbox', 
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-zygote',
                    '--single-process'
                ]
            }
            
            # Use explicit executable path if found
            if 'BROWSER_EXECUTABLE_PATH' in os.environ:
                launch_args['executable_path'] = os.environ['BROWSER_EXECUTABLE_PATH']
            
            browser = await asyncio.wait_for(
                p.chromium.launch(**launch_args),
                timeout=30.0
            )
            
            # Test basic functionality
            page = await browser.new_page()
            await page.goto('data:text/html,<h1>Test</h1>', timeout=10000)
            title = await page.title()
            await browser.close()
            
            logger.info(f"✅ Playwright functionality test passed - Page title: '{title}'")
            return True
            
    except Exception as e:
        logger.error(f"❌ Playwright functionality test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def install_browsers_lambda():
    """Install browsers specifically for Lambda environment"""
    try:
        setup_lambda_environment()
        
        logger.info("Installing Playwright browsers for Lambda...")
        
        # Change to the right directory
        os.chdir('/opt/python')
        
        # Set environment for installation
        env = os.environ.copy()
        env['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/python/pw-browsers'
        env['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '0'
        
        # Install browsers
        cmd = f"{sys.executable} -m playwright install chromium"
        
        logger.info(f"Running: {cmd}")
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            env=env
        )
        
        if result.returncode == 0:
            logger.info("✅ Browser installation completed successfully")
            logger.info(f"Output: {result.stdout}")
            
            # Verify installation
            if verify_browser_installation():
                logger.info("✅ Browser installation verified")
                return True
            else:
                logger.error("❌ Browser installation verification failed")
                return False
        else:
            logger.error(f"❌ Browser installation failed: {result.stderr}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error installing browsers: {e}")
        return False

async def ensure_browsers_ready():
    """Ensure browsers are ready for use in Lambda environment"""
    try:
        setup_lambda_environment()
        
        # Check if already installed and working
        if verify_browser_installation():
            logger.info("Browsers already installed, testing functionality...")
            if await test_playwright_functionality():
                logger.info("✅ Browsers ready and functional")
                return True
            else:
                logger.warning("Browsers installed but not functional, reinstalling...")
        
        # Install browsers
        if install_browsers_lambda():
            # Test functionality
            if await test_playwright_functionality():
                logger.info("✅ Browser installation and testing completed successfully")
                return True
            else:
                logger.error("❌ Browsers installed but functionality test failed")
                return False
        else:
            logger.error("❌ Browser installation failed")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error ensuring browsers ready: {e}")
        return False

def main():
    """Main entry point for browser installation"""
    logger.info("=" * 60)
    logger.info("FIXED PLAYWRIGHT INSTALLATION FOR AWS LAMBDA")
    logger.info("=" * 60)
    
    # Run async installation process
    try:
        result = asyncio.run(ensure_browsers_ready())
        
        if result:
            logger.info("🎉 Playwright installation completed successfully!")
            sys.exit(0)
        else:
            logger.error("💥 Playwright installation failed!")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"💥 Critical error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
