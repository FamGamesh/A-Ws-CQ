#!/usr/bin/env python3
"""
Chrome Binary Installation Script for AWS Lambda
Alternative approach avoiding GLIBC compatibility issues
"""

import os
import subprocess
import sys
import logging
from pathlib import Path

# Configure logging for Lambda environment
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def verify_chrome_binary():
    """Verify that Chrome binary is properly installed"""
    try:
        chrome_path = '/opt/chrome/chrome'
        
        if not os.path.exists(chrome_path):
            logger.error("Chrome binary doesn't exist")
            return False
        
        if not os.access(chrome_path, os.X_OK):
            logger.error("Chrome binary is not executable")
            # Try to fix permissions
            import stat
            try:
                os.chmod(chrome_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
                logger.info("Fixed Chrome binary permissions")
            except Exception as e:
                logger.error(f"Failed to fix permissions: {e}")
                return False
        
        # Test Chrome binary
        try:
            result = subprocess.run([chrome_path, '--version'], 
                                 capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                chrome_version = result.stdout.strip()
                logger.info(f"✅ Chrome binary verified: {chrome_version}")
                return True
            else:
                logger.error(f"Chrome version check failed: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Chrome binary test timeout")
            return False
        except Exception as e:
            logger.error(f"Chrome binary test failed: {e}")
            return False
        
    except Exception as e:
        logger.error(f"Error verifying Chrome binary: {e}")
        return False

def setup_chrome_environment():
    """Setup Chrome-specific environment variables"""
    os.environ['CHROME_BINARY_PATH'] = '/opt/chrome/chrome'
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '/opt/chrome'
    os.environ['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '1'
    os.environ['BROWSER_EXECUTABLE_PATH'] = '/opt/chrome/chrome'
    
    # Ensure directories exist with proper permissions
    os.makedirs('/opt/chrome', exist_ok=True)
    os.makedirs('/tmp/pdfs', exist_ok=True)
    
    logger.info("Chrome environment setup completed")

def download_chrome_binary():
    """Download Chrome binary compatible with Amazon Linux 2"""
    try:
        setup_chrome_environment()
        
        chrome_path = '/opt/chrome/chrome'
        
        if os.path.exists(chrome_path) and verify_chrome_binary():
            logger.info("Chrome binary already exists and is functional")
            return True
        
        logger.info("Downloading Chrome binary for Amazon Linux...")
        
        # Change to opt directory
        os.chdir('/opt')
        
        # Download compatible Chrome binary
        download_commands = [
            "wget -q https://github.com/adieuadieu/serverless-chrome/releases/download/v1.0.0-57/stable-headless-chromium-amazonlinux-2.zip",
            "curl -s -L -o stable-headless-chromium-amazonlinux-2.zip https://github.com/adieuadieu/serverless-chrome/releases/download/v1.0.0-57/stable-headless-chromium-amazonlinux-2.zip"
        ]
        
        download_success = False
        for cmd in download_commands:
            logger.info(f"Trying download: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
            
            if result.returncode == 0 and os.path.exists('/opt/stable-headless-chromium-amazonlinux-2.zip'):
                logger.info("✅ Chrome binary download successful")
                download_success = True
                break
            else:
                logger.warning(f"Download failed with: {cmd}")
        
        if not download_success:
            logger.error("All download methods failed")
            return False
        
        # Extract and setup Chrome binary
        extract_commands = [
            "unzip -q stable-headless-chromium-amazonlinux-2.zip",
            "mv headless-chromium /opt/chrome/chrome",
            "chmod +x /opt/chrome/chrome",
            "rm -f stable-headless-chromium-amazonlinux-2.zip"
        ]
        
        for cmd in extract_commands:
            logger.info(f"Running: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                logger.warning(f"Command warning: {cmd} - {result.stderr}")
                # Continue with other commands
        
        # Verify final installation
        if verify_chrome_binary():
            logger.info("✅ Chrome binary installation and verification completed")
            return True
        else:
            logger.error("❌ Chrome binary verification failed after installation")
            return False
            
    except Exception as e:
        logger.error(f"❌ Error downloading Chrome binary: {e}")
        return False

def test_chrome_with_playwright():
    """Test Chrome binary with Playwright"""
    try:
        logger.info("Testing Chrome binary with Playwright...")
        
        # Import Playwright
        from playwright.sync_api import sync_playwright
        
        chrome_path = '/opt/chrome/chrome'
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=chrome_path,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-zygote',
                    '--single-process'
                ]
            )
            
            page = browser.new_page()
            page.goto('data:text/html,<h1>Test</h1>', timeout=10000)
            title = page.title()
            browser.close()
            
            if title:
                logger.info(f"✅ Playwright-Chrome integration test passed")
                return True
            else:
                logger.error("❌ Playwright-Chrome integration test failed - no title")
                return False
        
    except Exception as e:
        logger.error(f"❌ Playwright-Chrome integration test failed: {e}")
        return False

def main():
    """Main entry point for Chrome binary setup"""
    logger.info("=" * 60)
    logger.info("CHROME BINARY SETUP FOR AWS LAMBDA")
    logger.info("=" * 60)
    
    try:
        # Setup environment
        setup_chrome_environment()
        
        # Check if Chrome binary already exists and works
        if verify_chrome_binary():
            logger.info("Chrome binary already functional")
            
            # Test with Playwright
            if test_chrome_with_playwright():
                logger.info("🎉 Chrome binary setup completed - all tests passed!")
                sys.exit(0)
            else:
                logger.warning("Chrome binary exists but Playwright integration failed")
        
        # Download and install Chrome binary
        if download_chrome_binary():
            # Test with Playwright
            if test_chrome_with_playwright():
                logger.info("🎉 Chrome binary installation and testing completed successfully!")
                sys.exit(0)
            else:
                logger.error("Chrome binary installed but Playwright integration failed")
                sys.exit(1)
        else:
            logger.error("Chrome binary installation failed")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"💥 Critical error in Chrome setup: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()