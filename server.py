from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
import os
from dotenv import load_dotenv
import requests
import asyncio
import json
from typing import List, Dict, Optional
import uuid
from datetime import datetime, timedelta
import re
from pathlib import Path
import logging
import pickle
import hashlib
import time
import threading
from bs4 import BeautifulSoup
import base64
import io
from PIL import Image as PILImage
import concurrent.futures
import aiohttp
import aiofiles

# Puppeteer imports for screenshot capture
try:
    from pyppeteer import launch, browser
    PUPPETEER_AVAILABLE = True
    print("✅ Puppeteer available for screenshot capture")
except ImportError:
    PUPPETEER_AVAILABLE = False
    print("⚠️ Puppeteer not available - screenshots will be simulated")

# Anti-Rate-Limiting imports
import random
from urllib.parse import urlparse
import itertools

from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Image as ReportLabImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.colors import HexColor, black, darkblue, darkgreen, darkred, white, lightgrey
from reportlab.graphics.shapes import Drawing, Rect, Line
from reportlab.platypus.flowables import Flowable
from reportlab.lib.utils import ImageReader

# Import S3 integration for Lambda - PRODUCTION VERSION
try:
    from s3_production_integration import get_pdf_directory, upload_pdf_to_s3_lambda, LAMBDA_S3_INTEGRATION
    print("✅ Production Lambda S3 integration loaded")
except ImportError:
    LAMBDA_S3_INTEGRATION = False
    print("ℹ️ Using standard file storage (non-Lambda environment)")
    
    def get_pdf_directory() -> Path:
        """Environment-aware PDF directory configuration."""
        cloud_env_indicators = [
            'DYNO', 'RENDER', 'VERCEL', 'RAILWAY_ENVIRONMENT', 
            'GOOGLE_CLOUD_PROJECT', 'AWS_LAMBDA_FUNCTION_NAME', 
            'AZURE_FUNCTIONS_ENVIRONMENT'
        ]
        
        is_cloud_environment = any(os.getenv(indicator) for indicator in cloud_env_indicators)
        is_container = os.path.exists('/.dockerenv') or os.path.exists('/proc/1/cgroup')
        
        app_dir_writable = False
        try:
            test_file = Path("/app/.write_test")
            test_file.touch()
            test_file.unlink()
            app_dir_writable = True
        except (PermissionError, OSError):
            app_dir_writable = False
        
        if is_cloud_environment or is_container or not app_dir_writable:
            pdf_dir = Path("/tmp/pdfs")
            print(f"🌤️  Using cloud-compatible PDF directory: {pdf_dir}")
        else:
            pdf_dir = Path("/app/pdfs")
            print(f"🏠 Using development PDF directory: {pdf_dir}")
        
        try:
            pdf_dir.mkdir(parents=True, exist_ok=True)
            print(f"✅ PDF directory ready: {pdf_dir}")
        except Exception as e:
            print(f"❌ Error creating PDF directory {pdf_dir}: {e}")
            if pdf_dir != Path("/tmp/pdfs"):
                print("🔄 Falling back to /tmp/pdfs...")
                pdf_dir = Path("/tmp/pdfs")
                pdf_dir.mkdir(parents=True, exist_ok=True)
                print(f"✅ Fallback PDF directory ready: {pdf_dir}")
            else:
                raise
        
        return pdf_dir

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LAMBDA-COMPATIBLE HTTP SCRAPING MANAGER
class LambdaHTTPScrapingManager:
    """
    Lambda-compatible HTTP scraping manager with 30 concurrent processes support
    Replaces Playwright browser automation with HTTP requests + BeautifulSoup
    """
    
    def __init__(self):
        self.session = None
        self.is_initialized = False
        self.scraping_stats = {
            "requests_made": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0,
            "concurrent_processes": 0
        }
        self.max_concurrent_processes = 30
        self.semaphore = asyncio.Semaphore(self.max_concurrent_processes)
        
    async def initialize(self):
        """Initialize HTTP session for Lambda environment"""
        if self.is_initialized:
            return
            
        try:
            # Create aiohttp session with optimized settings for Lambda
            timeout = aiohttp.ClientTimeout(total=15, connect=10)
            connector = aiohttp.TCPConnector(
                limit=50,  # Total connection limit
                limit_per_host=10,  # Per-host connection limit
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )
            
            self.session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Referer': 'https://testbook.com'
                }
            )
            
            self.is_initialized = True
            print("✅ Lambda HTTP Scraping Manager initialized for 30 concurrent processes")
            
        except Exception as e:
            print(f"❌ Error initializing HTTP Scraping Manager: {e}")
            raise
    
    async def scrape_testbook_page_concurrent(self, url: str, topic: str, exam_type: str = "SSC") -> Optional[dict]:
        """Scrape testbook page with concurrent processing support"""
        async with self.semaphore:
            self.scraping_stats["concurrent_processes"] += 1
            try:
                result = await self._scrape_testbook_page_internal(url, topic, exam_type)
                if result:
                    self.scraping_stats["successful_scrapes"] += 1
                else:
                    self.scraping_stats["failed_scrapes"] += 1
                return result
            finally:
                self.scraping_stats["concurrent_processes"] -= 1
    
    async def _scrape_testbook_page_internal(self, url: str, topic: str, exam_type: str) -> Optional[dict]:
        """Internal scraping with HTTP requests and BeautifulSoup"""
        try:
            if not self.is_initialized:
                await self.initialize()
            
            print(f"🔍 HTTP scraping: {url}")
            self.scraping_stats["requests_made"] += 1
            
            # Fetch page content with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    async with self.session.get(url) as response:
                        if response.status == 200:
                            html_content = await response.text()
                            break
                        else:
                            print(f"❌ HTTP {response.status} for {url}")
                            if attempt == max_retries - 1:
                                return None
                except Exception as e:
                    print(f"⚠️ Request attempt {attempt + 1} failed: {e}")
                    if attempt == max_retries - 1:
                        return None
                    await asyncio.sleep(1)
            
            # Parse HTML with BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract MCQ data
            mcq_data = await self._extract_mcq_from_soup(soup, url, topic, exam_type)
            
            return mcq_data
            
        except Exception as e:
            print(f"❌ Error scraping {url}: {e}")
            return None
    
    async def _extract_mcq_from_soup(self, soup: BeautifulSoup, url: str, topic: str, exam_type: str) -> Optional[dict]:
        """Extract MCQ data from BeautifulSoup with relevancy checks"""
        try:
            # Extract question
            question = ""
            question_selectors = [
                'h1.questionBody.tag-h1',
                'div.questionBody', 
                'h1.question',
                '.question-text'
            ]
            
            for selector in question_selectors:
                question_elem = soup.select_one(selector)
                if question_elem:
                    question = self._clean_text(question_elem.get_text())
                    break
            
            if not question or len(question.strip()) < 10:
                print(f"❌ No valid question found on {url}")
                return None
            
            # Check topic relevance
            if not self._is_topic_relevant(question, topic):
                print(f"❌ Question not relevant for topic '{topic}' on {url}")
                return None
            
            # Extract exam source information - combine heading and title
            exam_source_heading = ""
            exam_source_title = ""
            combined_exam_source = ""
            
            try:
                heading_elem = soup.select_one('div.pyp-heading')
                if heading_elem:
                    exam_source_heading = self._clean_text(heading_elem.get_text())
                
                title_elem = soup.select_one('div.pyp-title.line-ellipsis')
                if title_elem:
                    exam_source_title = self._clean_text(title_elem.get_text())
                
                # Combine heading and title to form complete exam source
                if exam_source_heading and exam_source_title:
                    combined_exam_source = f"{exam_source_heading} {exam_source_title}".strip()
                elif exam_source_heading:
                    combined_exam_source = exam_source_heading
                elif exam_source_title:
                    combined_exam_source = exam_source_title
                    
            except Exception as e:
                print(f"⚠️ Error extracting exam source: {e}")
            
            # Check exam type relevance using combined exam source
            if not self._is_exam_type_relevant(combined_exam_source, "", exam_type):
                print(f"❌ MCQ not relevant for exam type '{exam_type}' on {url}")
                return None
            
            # Extract options
            options = []
            option_elements = soup.select('li.option')
            for elem in option_elements:
                option_text = self._clean_text(elem.get_text())
                if option_text:
                    options.append(option_text)
            
            # Extract answer/solution
            answer = ""
            answer_elem = soup.select_one('.solution')
            if answer_elem:
                answer = self._clean_text(answer_elem.get_text())
            
            # Generate screenshot (Lambda-compatible approach)
            screenshot = await self._generate_screenshot_simulation(soup, url)
            
            # Return MCQ data if we have sufficient content
            if question and (options or answer):
                return {
                    "url": url,
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "exam_source_heading": exam_source_heading,
                    "exam_source_title": exam_source_title,
                    "combined_exam_source": combined_exam_source,
                    "screenshot": screenshot,
                    "is_relevant": True,
                    "scraping_method": "lambda_http_requests"
                }
            else:
                print(f"❌ Insufficient MCQ data extracted from {url}")
                return None
                
        except Exception as e:
            print(f"❌ Error extracting MCQ data from {url}: {e}")
            return None
    
    async def _generate_screenshot_simulation(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        """Generate real screenshot using Puppeteer or fallback to simulation"""
        try:
            # Try to capture real screenshot with Puppeteer
            if PUPPETEER_AVAILABLE:
                screenshot = await screenshot_manager.capture_screenshot(url, "")
                if screenshot:
                    # Convert to base64 for storage
                    screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
                    print(f"✅ Real screenshot captured using Puppeteer for {url}")
                    return screenshot_base64
                else:
                    print(f"⚠️ Puppeteer screenshot failed, falling back to simulation for {url}")
            else:
                print(f"⚠️ Puppeteer not available, using simulation for {url}")
            
            # Fallback: Create a simple text-based representation as base64 image
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a simple image representation
            img = Image.new('RGB', (800, 600), color='white')
            draw = ImageDraw.Draw(img)
            
            # Add URL text
            try:
                font = ImageFont.load_default()
            except:
                font = None
            
            draw.text((10, 10), f"URL: {url}", fill='black', font=font)
            draw.text((10, 30), "Lambda HTTP Scraping", fill='blue', font=font)
            draw.text((10, 50), "Screenshot simulation (Puppeteer unavailable)", fill='gray', font=font)
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            screenshot_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            return screenshot_base64
            
        except Exception as e:
            print(f"⚠️ Error generating screenshot: {e}")
            return None
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text"""
        if not text:
            return ""
        
        # Remove extra whitespace and clean up
        text = ' '.join(text.split())
        
        # Remove unwanted patterns
        unwanted_patterns = [
            "Download Solution PDF", "Download PDF", "Attempt Online",
            "View all BPSC Exam Papers >", "View all SSC Exam Papers >",
            "View all BPSC Exam Papers", "View all SSC Exam Papers"
        ]
        
        for pattern in unwanted_patterns:
            text = text.replace(pattern, "")
        
        return text.strip()
    
    def _is_topic_relevant(self, question: str, topic: str) -> bool:
        """Check if question is relevant to the topic"""
        try:
            from competitive_exam_keywords import enhanced_is_mcq_relevant
            return enhanced_is_mcq_relevant(question, topic)
        except ImportError:
            # Fallback simple relevance check
            question_lower = question.lower()
            topic_lower = topic.lower()
            
            # Split topic into words for flexible matching
            topic_words = topic_lower.split()
            
            # Check if any topic words appear in question
            for word in topic_words:
                if len(word) > 2 and word in question_lower:
                    return True
            
            return False
    
    def _is_exam_type_relevant(self, combined_source: str, unused_param: str, exam_type: str) -> bool:
        """Check if MCQ is relevant for the specified exam type"""
        exam_text = combined_source.lower() if combined_source else ""
        exam_type_lower = exam_type.lower()
        
        # Define exam type keywords
        exam_keywords = {
            "ssc": ["ssc", "staff selection commission", "ssc cgl", "ssc chsl", "ssc mts", "ssc je"],
            "bpsc": ["bpsc", "bihar public service commission", "bpsc combined", "bpsc prelims"],
            "upsc": ["upsc", "union public service commission", "civil services", "ias", "ips"],
            "railway": ["railway", "rrb", "railway recruitment board", "ntpc", "group d"],
            "banking": ["bank", "banking", "ibps", "sbi", "rbi", "nabard"]
        }
        
        # Get keywords for the specified exam type
        relevant_keywords = exam_keywords.get(exam_type_lower, [exam_type_lower])
        
        # Check if any relevant keywords are present
        for keyword in relevant_keywords:
            if keyword in exam_text:
                return True
        
        return False
    
    async def scrape_testbook_page_with_stealth(self, url: str, topic: str, exam_type: str = "SSC") -> Optional[dict]:
        """
        Enhanced scraping with stealth manager integration for anti-rate-limiting
        """
        try:
            print(f"🕵️ Stealth scraping: {url}")
            
            # Use stealth manager for request
            success, html_content, status_code = await stealth_manager.stealth_request(url, max_attempts=3)
            
            if not success or not html_content:
                print(f"❌ Stealth request failed for {url} (status: {status_code})")
                return None
            
            # Parse with BeautifulSoup  
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract MCQ data using existing logic
            mcq_data = await self._extract_mcq_from_soup(soup, url, topic, exam_type)
            
            if mcq_data and mcq_data.get('is_relevant'):
                print(f"✅ Stealth extraction successful: {url}")
                return mcq_data
            else:
                print(f"⚠️ MCQ not relevant or extraction failed: {url}")
                return None
                
        except Exception as e:
            print(f"❌ Error in stealth scraping for {url}: {str(e)}")
            return None

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            print("✅ HTTP scraping session closed")
            await self.session.close()
            self.session = None
            self.is_initialized = False
    
    def get_stats(self) -> dict:
        """Get scraping statistics"""
        return {
            "initialized": self.is_initialized,
            "scraping_stats": self.scraping_stats,
            "max_concurrent_processes": self.max_concurrent_processes,
            "success_rate": (
                self.scraping_stats["successful_scrapes"] / 
                max(self.scraping_stats["requests_made"], 1) * 100
            )
        }

# Global HTTP scraping manager
http_scraper = LambdaHTTPScrapingManager()

# INTELLIGENT ANTI-RATE-LIMITING STEALTH SYSTEM
class StealthRateLimitManager:
    """
    Intelligent anti-rate-limiting system to ensure no MCQ is left behind
    Implements stealth techniques, retry mechanisms, and smart request distribution
    """
    
    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0',
            'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0'
        ]
        
        self.session_pool = []
        self.current_session_index = 0
        self.failed_urls = set()
        self.retry_queue = []
        self.request_delays = {
            'base_delay': (2, 5),        # 2-5 seconds base delay
            'retry_delay': (5, 10),      # 5-10 seconds on retry
            'rate_limit_delay': (15, 30) # 15-30 seconds on rate limit
        }
        self.max_retries_per_url = 5
        self.url_retry_count = {}
        
    async def initialize_session_pool(self, pool_size: int = 5):
        """Initialize multiple aiohttp sessions for rotation"""
        print(f"🕵️ Initializing stealth session pool with {pool_size} sessions...")
        
        for i in range(pool_size):
            # Random user agent and headers for each session
            user_agent = random.choice(self.user_agents)
            
            # Stealth headers that mimic real browsers
            headers = {
                'User-Agent': user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': random.choice([
                    'en-US,en;q=0.9,hi;q=0.8',
                    'en-GB,en;q=0.9',
                    'en-US,en;q=0.5'
                ]),
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Cache-Control': 'max-age=0',
                'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': random.choice(['"Windows"', '"macOS"', '"Linux"'])
            }
            
            # Random referrer
            if random.choice([True, False]):
                headers['Referer'] = random.choice([
                    'https://www.google.com/',
                    'https://www.bing.com/', 
                    'https://duckduckgo.com/',
                    'https://testbook.com/'
                ])
            
            # Create timeout configuration with proper connection timeout
            timeout = aiohttp.ClientTimeout(
                total=30, 
                connect=random.uniform(10, 15),  # Connection timeout moved to ClientTimeout
                sock_read=20,
                sock_connect=15
            )
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=5,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True
            )
            
            session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers=headers,
                cookie_jar=aiohttp.CookieJar(unsafe=True)  # Handle cookies like a real browser
            )
            
            self.session_pool.append(session)
            
        print(f"✅ Stealth session pool initialized with {len(self.session_pool)} diverse sessions")
    
    def get_next_session(self):
        """Get the next session from the pool with rotation"""
        if not self.session_pool:
            return None
        
        session = self.session_pool[self.current_session_index]
        self.current_session_index = (self.current_session_index + 1) % len(self.session_pool)
        return session
    
    async def smart_delay(self, delay_type: str = 'base_delay', url: str = None):
        """Apply intelligent delays to avoid detection"""
        delay_range = self.request_delays[delay_type]
        base_delay = random.uniform(delay_range[0], delay_range[1])
        
        # Add extra delay for URLs that have failed before
        if url and url in self.failed_urls:
            extra_delay = random.uniform(2, 5)
            base_delay += extra_delay
            
        # Add small random jitter
        jitter = random.uniform(0.5, 1.5)
        final_delay = base_delay + jitter
        
        print(f"⏳ Smart delay: {final_delay:.2f}s ({delay_type})")
        await asyncio.sleep(final_delay)
    
    async def stealth_request(self, url: str, max_attempts: int = 3):
        """
        Make a stealth request with anti-rate-limiting measures
        Returns (success: bool, response_text: str or None, status_code: int)
        """
        if not self.session_pool:
            await self.initialize_session_pool()
        
        # Track retry attempts
        retry_count = self.url_retry_count.get(url, 0)
        
        for attempt in range(max_attempts):
            try:
                # Get a session from the pool
                session = self.get_next_session()
                if not session:
                    return False, None, 0
                
                # Apply smart delay before request (removed - stealth methods already sufficient)
                if attempt > 0:
                    await asyncio.sleep(0.5)  # Minimal delay for retries only
                
                print(f"🕵️ Stealth request attempt {attempt + 1}: {url}")
                
                async with session.get(url) as response:
                    if response.status == 200:
                        html_content = await response.text()
                        # Reset retry count on success
                        if url in self.url_retry_count:
                            del self.url_retry_count[url]
                        if url in self.failed_urls:
                            self.failed_urls.discard(url)
                        print(f"✅ Stealth request successful: {url}")
                        return True, html_content, 200
                    
                    elif response.status == 429:
                        print(f"🚫 Rate limited (429) on attempt {attempt + 1}: {url}")
                        self.failed_urls.add(url)
                        
                        # Try with a different session (removed extensive delays)
                        continue
                    
                    elif response.status in [403, 404, 502, 503]:
                        print(f"⚠️ HTTP {response.status} on attempt {attempt + 1}: {url}")
                        continue
                    
                    else:
                        print(f"❌ Unexpected status {response.status}: {url}")
                        continue
            
            except asyncio.TimeoutError:
                print(f"⏱️ Timeout on attempt {attempt + 1}: {url}")
                continue
                
            except Exception as e:
                print(f"💥 Error on attempt {attempt + 1} for {url}: {str(e)}")
                continue
        
        # All attempts failed - add to retry queue
        self.url_retry_count[url] = retry_count + 1
        if retry_count < self.max_retries_per_url:
            self.retry_queue.append(url)
            print(f"📝 Added to retry queue: {url} (attempt #{retry_count + 1})")
        else:
            print(f"❌ Giving up on {url} after {self.max_retries_per_url} total retries")
        
        return False, None, 0
    
    async def process_retry_queue(self, topic: str, exam_type: str):
        """Process failed URLs with even more aggressive anti-rate-limiting"""
        if not self.retry_queue:
            return []
        
        print(f"🔄 Processing retry queue with {len(self.retry_queue)} failed URLs...")
        print("🕵️ Applying maximum stealth mode...")
        
        retry_results = []
        processed_urls = []
        
        for url in self.retry_queue.copy():
            if url in processed_urls:
                continue
                
            retry_count = self.url_retry_count.get(url, 0)
            if retry_count >= self.max_retries_per_url:
                continue
            
            # Ultra-conservative delay for retries
            await self.smart_delay('rate_limit_delay', url)
            
            success, html_content, status_code = await self.stealth_request(url, max_attempts=2)
            
            if success and html_content:
                # Parse with BeautifulSoup and extract MCQ
                soup = BeautifulSoup(html_content, 'html.parser')
                mcq_data = await self._extract_mcq_from_soup(soup, url, topic, exam_type)
                
                if mcq_data:
                    retry_results.append(mcq_data)
                    print(f"✅ Retry successful: {url}")
                    self.retry_queue.remove(url)
            
            processed_urls.append(url)
        
        print(f"✅ Retry queue processed: {len(retry_results)} MCQs recovered")
        return retry_results
    
    async def _extract_mcq_from_soup(self, soup: BeautifulSoup, url: str, topic: str, exam_type: str):
        """Extract MCQ data from BeautifulSoup - mirror of http_scraper method"""
        try:
            # Use the same extraction logic as http_scraper
            return await http_scraper._extract_mcq_from_soup(soup, url, topic, exam_type)
        except Exception as e:
            print(f"❌ Error extracting MCQ data from {url}: {e}")
            return None
    
    async def close_all_sessions(self):
        """Close all sessions in the pool"""
        for session in self.session_pool:
            try:
                await session.close()
            except:
                pass
        self.session_pool.clear()
        print("✅ All stealth sessions closed")

# Global stealth rate limit manager
stealth_manager = StealthRateLimitManager()

# PUPPETEER SCREENSHOT MANAGER - AWS Lambda Compatible
class PuppeteerScreenshotManager:
    """AWS Lambda compatible screenshot manager using Puppeteer"""
    
    def __init__(self):
        self.browser = None
        self.is_initialized = False
        self.screenshot_stats = {
            "screenshots_captured": 0,
            "screenshot_failures": 0,
            "browser_launches": 0
        }
    
    async def initialize(self):
        """Initialize Puppeteer browser for Lambda environment with stealth features"""
        if not PUPPETEER_AVAILABLE:
            print("❌ Puppeteer not available for screenshot capture")
            return False
        
        if self.is_initialized and self.browser:
            return True
        
        try:
            print("🚀 Launching Puppeteer browser for AWS Lambda with stealth features...")
            
            # Enhanced AWS Lambda + stealth optimized browser args
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox', 
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--disable-gpu-sandbox',
                '--disable-software-rasterizer',
                '--no-first-run',
                '--no-zygote',
                '--single-process',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-extensions',
                '--disable-plugins',
                '--disable-default-apps',
                '--disable-background-networking',
                '--disable-sync',
                '--no-default-browser-check',
                '--memory-pressure-off',
                '--max_old_space_size=512',
                '--aggressive-cache-discard',
                '--disable-hang-monitor',
                '--disable-prompt-on-repost',
                '--disable-client-side-phishing-detection',
                '--headless',
                '--hide-scrollbars',
                '--mute-audio',
                # LAMBDA-COMPATIBLE PATHS
                '--user-data-dir=/tmp/puppeteer_cache',
                '--data-path=/tmp/puppeteer_data',
                '--disk-cache-dir=/tmp/puppeteer_cache',
                '--homedir=/tmp',
                # STEALTH FEATURES
                '--disable-blink-features=AutomationControlled',
                '--disable-features=VizDisplayCompositor',
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-ipc-flooding-protection',
                '--disable-component-extensions-with-background-pages'
            ]
            
            # Lambda-compatible Chromium execution - Disable for Lambda environment
            if os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
                print("🚫 Lambda environment detected - Puppeteer disabled (not supported)")
                self.is_initialized = False
                return False
            
            launch_options = {
                'headless': True,
                'args': browser_args,
                'autoClose': False,
                'dumpio': False,
                'devtools': False
            }
            
            # Try different Chromium paths for Lambda environment
            chromium_paths = [
                '/opt/python/bin/chromium',
                '/opt/chromium/chromium',
                '/usr/bin/chromium-browser',
                '/usr/bin/google-chrome',
                None  # Use pyppeteer's bundled Chromium
            ]
            
            browser_launched = False
            for chromium_path in chromium_paths:
                try:
                    if chromium_path:
                        launch_options['executablePath'] = chromium_path
                    else:
                        launch_options.pop('executablePath', None)  # Use bundled
                    
                    print(f"🔧 Trying Chromium path: {chromium_path or 'bundled'}")
                    self.browser = await launch(**launch_options)
                    browser_launched = True
                    print(f"✅ Puppeteer browser launched successfully with {chromium_path or 'bundled'} Chromium")
                    break
                    
                except Exception as path_error:
                    print(f"⚠️ Failed with {chromium_path or 'bundled'}: {path_error}")
                    continue
            
            if not browser_launched:
                print("❌ All Chromium paths failed, Puppeteer unavailable")
                self.is_initialized = False
                return False
            
            self.is_initialized = True
            self.screenshot_stats["browser_launches"] += 1
            print("✅ Puppeteer browser launched successfully with stealth features")
            return True
            
        except Exception as e:
            print(f"❌ Error launching Puppeteer browser: {e}")
            self.is_initialized = False
            return False
    
    async def capture_screenshot(self, url: str, topic: str) -> Optional[bytes]:
        """
        Capture optimized MCQ screenshot with stealth features and rate limiting avoidance
        """
        if not await self.initialize():
            print(f"❌ Puppeteer browser not available for {url}")
            return None
        
        page = None
        try:
            print(f"📸 Capturing stealth screenshot with 85% zoom for URL: {url}")
            
            # Create new page with stealth settings
            page = await self.browser.newPage()
            
            # STEALTH CONFIGURATION
            # Set random realistic viewport
            viewport_options = [
                {'width': 1366, 'height': 768},  # Common laptop
                {'width': 1920, 'height': 1080}, # Common desktop
                {'width': 1440, 'height': 900},  # MacBook Air
                {'width': 1536, 'height': 864},  # Common laptop
                {'width': 1600, 'height': 900}   # Wide screen
            ]
            
            viewport = random.choice(viewport_options)
            await page.setViewport(viewport)
            
            # Set realistic user agent and headers to avoid detection
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0'
            ]
            
            await page.setUserAgent(random.choice(user_agents))
            
            # Set additional headers
            await page.setExtraHTTPHeaders({
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            
            # Block resources to speed up loading and avoid detection
            await page.setRequestInterception(True)
            
            async def handle_request(request):
                resource_type = request.resourceType
                if resource_type in ['image', 'stylesheet', 'font', 'media']:
                    await request.abort()
                else:
                    await request.continue_()
            
            page.on('request', handle_request)
            
            # Random delay before navigation (mimic human behavior)
            await asyncio.sleep(random.uniform(1, 3))
            
            # Navigate to URL with timeout
            try:
                print(f"🌐 Navigating to: {url}")
                response = await page.goto(
                    url, 
                    waitUntil='networkidle2', 
                    timeout=45000  # 45 seconds timeout
                )
                
                if not response:
                    print(f"❌ No response received for {url}")
                    return None
                    
                if response.status >= 400:
                    print(f"❌ HTTP {response.status} error for {url}")
                    return None
                    
            except Exception as e:
                print(f"❌ Navigation failed for {url}: {e}")
                return None
            
            # Wait for page content to load
            try:
                await page.waitForSelector('body', timeout=10000)
                print(f"✅ Page content loaded for {url}")
            except:
                print(f"⚠️ Timeout waiting for body selector, proceeding anyway")
            
            # Remove anti-bot detection scripts and set zoom level
            await page.evaluate("""
                () => {
                    // Remove common anti-bot detection elements
                    const scripts = document.querySelectorAll('script');
                    scripts.forEach(script => {
                        const src = script.src || '';
                        const content = script.innerHTML || '';
                        if (src.includes('bot') || src.includes('protection') || 
                            content.includes('webdriver') || content.includes('automation')) {
                            script.remove();
                        }
                    });
                    
                    // Set zoom level for better MCQ visibility (85% zoom)
                    document.body.style.zoom = '0.85';
                    
                    // Remove overlays and modals that might block content
                    const overlays = document.querySelectorAll('[class*="overlay"], [class*="modal"], [id*="popup"]');
                    overlays.forEach(overlay => {
                        if (overlay.style.position === 'fixed' || overlay.style.position === 'absolute') {
                            overlay.style.display = 'none';
                        }
                    });
                }
            """)
            
            # Additional wait for dynamic content
            await asyncio.sleep(random.uniform(2, 4))
            
            # Take screenshot with high quality settings
            screenshot_options = {
                'fullPage': False,  # Only visible area for faster processing
                'quality': 85,     # High quality for readability
                'type': 'png',     # PNG for better text clarity
                'omitBackground': False
            }
            
            screenshot_buffer = await page.screenshot(screenshot_options)
            
            if screenshot_buffer:
                self.screenshot_stats["screenshots_captured"] += 1
                
                # Log screenshot info
                screenshot_size = len(screenshot_buffer)
                print(f"✅ Screenshot captured successfully: {url}")
                print(f"📊 Screenshot size: {screenshot_size} bytes ({screenshot_size/1024:.1f} KB)")
                
                # Random delay before closing to mimic human behavior
                await asyncio.sleep(random.uniform(0.5, 1.5))
                
                return screenshot_buffer
            else:
                print(f"❌ Empty screenshot buffer for {url}")
                return None
                
        except Exception as e:
            self.screenshot_stats["screenshot_failures"] += 1
            print(f"❌ Screenshot capture error for {url}: {e}")
            return None
            
        finally:
            if page:
                try:
                    await page.close()
                    print(f"🔒 Page closed for {url}")
                except:
                    pass
    
    async def close(self):
        """Close the browser"""
        if self.browser:
            try:
                await self.browser.close()
                self.browser = None
                self.is_initialized = False
                print("✅ Puppeteer browser closed")
            except:
                pass
    
    def get_stats(self) -> dict:
        """Get screenshot statistics"""
        total_attempts = self.screenshot_stats["screenshots_captured"] + self.screenshot_stats["screenshot_failures"]
        success_rate = (self.screenshot_stats["screenshots_captured"] / max(total_attempts, 1)) * 100
        
        return {
            "initialized": self.is_initialized,
            "screenshot_stats": self.screenshot_stats,
            "success_rate": f"{success_rate:.1f}%",
            "browser_available": PUPPETEER_AVAILABLE
        }

# Global screenshot manager
screenshot_manager = PuppeteerScreenshotManager()

# Define request models
class SearchRequest(BaseModel):
    topic: str
    exam_type: str = "SSC"
    max_mcqs: int = 25
    pdf_format: str = "image"

class MCQData(BaseModel):
    url: str
    question: str
    options: List[str] = []
    answer: str = ""
    exam_source_heading: str = ""
    exam_source_title: str = ""
    combined_exam_source: str = ""
    screenshot: Optional[str] = None  # Base64 encoded screenshot
    is_relevant: bool = True
    scraping_method: str = "http_requests"

# Global variables for job tracking
job_status = {}
job_results = {}

# Google Search API Manager
class GoogleSearchAPIManager:
    """Manage Google Search API with multiple keys for rate limiting"""
    
    def __init__(self):
        self.api_keys = []
        self.current_key_index = 0
        self.search_engine_id = os.getenv('SEARCH_ENGINE_ID', '2701a7d64a00d47fd')
        self.daily_limits = {}  # Track daily usage per key
        self.load_api_keys()
    
    def load_api_keys(self):
        """Load API keys from environment"""
        try:
            # Try to load from pool first
            api_key_pool = os.getenv('API_KEY_POOL', '')
            if api_key_pool:
                keys = [key.strip() for key in api_key_pool.split(',') if key.strip()]
                self.api_keys.extend(keys)
            
            # Also add individual key if available
            single_key = os.getenv('GOOGLE_API_KEY', '')
            if single_key and single_key not in self.api_keys:
                self.api_keys.append(single_key)
            
            print(f"📊 Loaded {len(self.api_keys)} Google API keys")
            
        except Exception as e:
            print(f"⚠️ Error loading Google API keys: {e}")
    
    def get_next_api_key(self):
        """Get next available API key with rate limiting"""
        if not self.api_keys:
            return None
        
        # Simple round-robin for now
        api_key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return api_key
    
    async def search_testbook_mcqs(self, topic: str, exam_type: str, max_results: int = 100):
        """Search for Testbook MCQ URLs using Google API"""
        api_key = self.get_next_api_key()
        if not api_key:
            print("❌ No Google API key available")
            return []
        
        try:
            # Construct search query
            query = f'site:testbook.com "{topic}" "{exam_type}" MCQ OR questions OR "previous year"'
            
            # Make request to Google Custom Search API
            url = "https://www.googleapis.com/customsearch/v1"
            params = {
                'key': api_key,
                'cx': self.search_engine_id,
                'q': query,
                'num': min(max_results, 100),  # Google limits to 100 per request
                'start': 1
            }
            # Create headers to avoid referrer issues
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://www.google.com/',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'cross-site'
            }
            
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        items = data.get('items', [])
                        
                        urls = []
                        for item in items:
                            url = item.get('link', '')
                            if 'testbook.com' in url and any(keyword in url.lower() for keyword in ['question', 'mcq', 'quiz', 'test']):
                                urls.append(url)
                        
                        print(f"🔍 Google Search found {len(urls)} Testbook MCQ URLs for '{topic}'")
                        return urls
                    elif response.status == 403:
                        error_data = await response.text()
                        print(f"❌ Google Search API 403 Error: {error_data}")
                        
                        # Check if this is a referrer blocking issue
                        if "API_KEY_HTTP_REFERRER_BLOCKED" in error_data or "referer" in error_data.lower():
                            print(f"🚫 API key blocked due to referrer restrictions")
                            print(f"💡 This API key is configured with HTTP referrer restrictions")
                            print(f"🔧 Consider configuring API key without referrer restrictions for server-side use")
                        else:
                            print(f"💡 This typically means API key quota exceeded or invalid API key")
                        
                        print(f"🔑 Using API key: {api_key[:10]}...")
                        
                        # Try next API key if available
                        if len(self.api_keys) > 1:
                            print("🔄 Trying next API key...")
                            self.api_keys.remove(api_key)
                            if self.api_keys:
                                return await self.search_testbook_mcqs(topic, exam_type, max_results)
                        else:
                            print("❌ No more API keys available")
                            print("💡 All API keys may have referrer restrictions or quota issues")
                            print("🔧 Please configure at least one API key without referrer restrictions")
                        return []
                    else:
                        error_data = await response.text()
                        print(f"❌ Google Search API error: {response.status}")
                        print(f"📋 Error details: {error_data}")
                        return []
                        
        except Exception as e:
            print(f"❌ Error in Google Search: {e}")
            return []

# Global API manager
api_key_manager = GoogleSearchAPIManager()

# Initialize async components
async def initialize_all():
    """Initialize all async components"""
    try:
        await http_scraper.initialize()
        await stealth_manager.initialize_session_pool()
        print("✅ All async components initialized")
    except Exception as e:
        print(f"❌ Error initializing components: {e}")

# Cleanup function
async def cleanup_all():
    """Cleanup all resources"""
    try:
        await http_scraper.close()
        await stealth_manager.close_all_sessions()
        if screenshot_manager.is_initialized:
            await screenshot_manager.close()
        print("✅ All resources cleaned up")
    except Exception as e:
        print(f"⚠️ Error during cleanup: {e}")

# Ensure cleanup on shutdown
async def lifespan(app):
    """Application lifespan manager"""
    # Startup
    await initialize_all()
    yield
    # Shutdown
    await cleanup_all()

# Background cleanup
async def cleanup_old_jobs():
    """Clean up jobs older than 1 hour"""
    try:
        cutoff_time = datetime.now() - timedelta(hours=1)
        jobs_to_remove = []
        
        for job_id, job_data in job_status.items():
            job_time = job_data.get('created_at', datetime.now())
            if isinstance(job_time, str):
                job_time = datetime.fromisoformat(job_time.replace('Z', '+00:00'))
                
            if job_time < cutoff_time:
                jobs_to_remove.append(job_id)
        
        for job_id in jobs_to_remove:
            job_status.pop(job_id, None)
            job_results.pop(job_id, None)
            
        if jobs_to_remove:
            print(f"🗑️ Cleaned up {len(jobs_to_remove)} old jobs")
            
    except Exception as e:
        print(f"⚠️ Error during job cleanup: {e}")

# Run periodic cleanup
async def periodic_cleanup():
    """Run cleanup every 30 minutes"""
    while True:
        try:
            await asyncio.sleep(1800)  # 30 minutes
            await cleanup_old_jobs()
        except Exception as e:
            print(f"⚠️ Error in periodic cleanup: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes before retrying

# Start background tasks
def start_background_tasks():
    """Start background tasks"""
    asyncio.create_task(periodic_cleanup())
    
    # Cleanup stealth sessions periodically
    asyncio.create_task(stealth_cleanup())

async def stealth_cleanup():
    """Cleanup stealth manager sessions periodically"""
    while True:
        try:
            await asyncio.sleep(3600)  # 1 hour
            await stealth_manager.close_all_sessions()
            await stealth_manager.initialize_session_pool()
            print("🔄 Stealth sessions refreshed")
        except:
            await asyncio.sleep(1800)  # Wait 30 minutes before retrying

# FastAPI App Configuration
app = FastAPI(title="Lambda MCQ Scraper", version="4.0.0")

# CORS Configuration for AWS Lambda - FIXED
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=False,  # CRITICAL FIX: Set to False when using allow_origins=["*"]
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# CORS Headers for Lambda responses - FIXED
def add_cors_headers(response_data):
    """Add CORS headers to response for AWS Lambda"""
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With, Accept, Origin, X-Amz-Date, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent",
        "Access-Control-Allow-Credentials": "false",  # CRITICAL FIX: Consistent with middleware
        "Access-Control-Max-Age": "86400",
        "Content-Type": "application/json"
    }
    return JSONResponse(content=response_data, headers=headers)

# API Endpoints
@app.get("/")
async def read_root():
    """Root endpoint"""
    response_data = {
        "message": "🚀 Lambda MCQ Scraper API - CORS Fixed",
        "version": "4.0.1",
        "approach": "http_requests_scraping",
        "browser_required": False,
        "cors_enabled": True,
        "cors_fixed": True,
        "status": "running",
        "environment": "lambda" if os.environ.get('AWS_LAMBDA_FUNCTION_NAME') else "development",
        "scraping_capabilities": {
            "concurrent_requests": 30,
            "stealth_mode": True,
            "screenshot_simulation": True,
            "anti_rate_limiting": True
        },
        "available_endpoints": [
            "GET /",
            "GET /api/health", 
            "POST /api/generate-mcq-pdf",
            "GET /api/job-status/{job_id}",
            "GET /api/download-pdf/{filename}",
            "OPTIONS /* (CORS preflight)"
        ]
    }
    return add_cors_headers(response_data)

@app.get("/api/health")
async def health_check():
    """Health check endpoint with Puppeteer screenshot support"""
    try:
        # Get component stats
        scraper_stats = http_scraper.get_stats()
        screenshot_stats = screenshot_manager.get_stats()
        
        response_data = {
            "status": "healthy",
            "cors_enabled": True,
            "cors_fixed": True,
            "timestamp": datetime.now().isoformat(),
            "environment": "lambda" if os.environ.get('AWS_LAMBDA_FUNCTION_NAME') else "development",
            "components": {
                "http_scraper": {
                    "initialized": scraper_stats["initialized"],
                    "success_rate": scraper_stats["success_rate"],
                    "requests_made": scraper_stats["scraping_stats"]["requests_made"],
                    "successful_scrapes": scraper_stats["scraping_stats"]["successful_scrapes"]
                },
                "screenshot_manager": {
                    "available": PUPPETEER_AVAILABLE,
                    "initialized": screenshot_stats["initialized"],
                    "success_rate": screenshot_stats["success_rate"] if screenshot_stats["initialized"] else "N/A"
                },
                "stealth_manager": {
                    "session_pool_size": len(stealth_manager.session_pool),
                    "failed_urls": len(stealth_manager.failed_urls),
                    "retry_queue_size": len(stealth_manager.retry_queue)
                }
            },
            "active_jobs": len(job_status),
            "job_results_cached": len(job_results)
        }
        
        return add_cors_headers(response_data)
        
    except Exception as e:
        error_data = {
            "status": "error",
            "cors_enabled": True,
            "cors_fixed": True,
            "message": str(e),
            "timestamp": datetime.now().isoformat()
        }
        return add_cors_headers(error_data)

@app.get("/api/test-search")
async def test_search_functionality():
    """Test search functionality"""
    try:
        # Test basic HTTP request capability
        async with aiohttp.ClientSession() as session:
            async with session.get("https://httpbin.org/json") as response:
                if response.status == 200:
                    test_data = await response.json()
                    return {
                        "message": "HTTP client working correctly",
                        "test_response": test_data,
                        "status": "success"
                    }
                else:
                    return {"message": "HTTP test failed", "status": "error"}
                    
    except Exception as e:
        return {
            "message": f"Test failed: {str(e)}",
            "status": "error",
            "scraper_initialized": http_scraper.is_initialized,
            "api_keys_available": len(api_key_manager.api_keys)
        }

@app.post("/api/generate-mcq-pdf")
async def generate_mcq_pdf(request: SearchRequest, background_tasks: BackgroundTasks):
    """Generate MCQ PDF with concurrent processing"""
    job_id = str(uuid.uuid4())
    
    try:
        # Initialize job status
        job_status[job_id] = {
            "status": "started",
            "created_at": datetime.now().isoformat(),
            "topic": request.topic,
            "exam_type": request.exam_type,
            "max_mcqs": request.max_mcqs,
            "pdf_format": request.pdf_format,
            "progress": 0,
            "stage": "initialization"
        }
        
        print(f"🚀 MCQ PDF generation started: {job_id}")
        print(f"📋 Request: {request.topic} ({request.exam_type}) - {request.max_mcqs} MCQs")
        
        # Start background processing
        background_tasks.add_task(
            process_mcq_request, 
            job_id, 
            request.topic, 
            request.exam_type, 
            request.max_mcqs,
            request.pdf_format
        )
        
        response_data = {
            "job_id": job_id,
            "status": "started", 
            "message": f"MCQ PDF generation started for topic: {request.topic}",
            "topic": request.topic,
            "exam_type": request.exam_type,
            "max_mcqs": request.max_mcqs,
            "pdf_format": request.pdf_format,
            "estimated_completion_time": "2-5 minutes",
            "check_status_endpoint": f"/api/job-status/{job_id}"
        }
        
        return add_cors_headers(response_data)
        
    except Exception as e:
        job_status[job_id] = {"status": "error", "error": str(e)}
        error_data = {"error": str(e), "job_id": job_id}
        return add_cors_headers(error_data)

@app.get("/api/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get job status"""
    try:
        if job_id in job_status:
            status_data = job_status[job_id].copy()
            
            # Add results if available
            if job_id in job_results and job_status[job_id].get("status") == "completed":
                status_data["results"] = job_results[job_id]
            
            return add_cors_headers(status_data)
        else:
            error_data = {"error": "Job not found", "job_id": job_id}
            return add_cors_headers(error_data)
            
    except Exception as e:
        error_data = {"error": str(e), "job_id": job_id}
        return add_cors_headers(error_data)

# Explicit OPTIONS handlers for CORS preflight requests
@app.options("/")
async def options_root():
    """Handle CORS preflight for root endpoint"""
    return add_cors_headers({"message": "CORS preflight successful"})

@app.options("/api/health")
async def options_health():
    """Handle CORS preflight for health endpoint"""
    return add_cors_headers({"message": "CORS preflight successful"})

@app.options("/api/generate-mcq-pdf")
async def options_generate_pdf():
    """Handle CORS preflight for PDF generation endpoint"""
    return add_cors_headers({"message": "CORS preflight successful"})

@app.options("/api/job-status/{job_id}")
async def options_job_status(job_id: str):
    """Handle CORS preflight for job status endpoint"""
    return add_cors_headers({"message": "CORS preflight successful"})

@app.get("/api/download-pdf/{filename}")
async def download_pdf(filename: str):
    """Download generated PDF"""
    try:
        pdf_dir = get_pdf_directory()
        file_path = pdf_dir / filename
        
        if file_path.exists():
            headers = {
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Credentials": "false",  # CORS FIX
            }
            
            return FileResponse(
                path=str(file_path),
                filename=filename,
                media_type='application/pdf',
                headers=headers
            )
        else:
            raise HTTPException(status_code=404, detail="PDF file not found")
    except Exception as e:
        print(f"❌ Error serving PDF file: {e}")
        raise HTTPException(status_code=500, detail="Error serving PDF file")

@app.get("/api/cleanup-old-jobs")
async def cleanup_old_jobs_endpoint():
    """Manual cleanup of old jobs"""
    try:
        await cleanup_old_jobs()
        return add_cors_headers({"message": "Cleanup completed", "status": "success"})
    except Exception as e:
        return add_cors_headers({"error": str(e), "status": "error"})

# Background processing functions
async def process_mcq_request(job_id: str, topic: str, exam_type: str, max_mcqs: int, pdf_format: str):
    """Process MCQ request in background"""
    try:
        job_status[job_id]["stage"] = "searching"
        job_status[job_id]["progress"] = 10
        
        # Search and filter MCQs
        mcqs = await search_and_filter_mcqs(topic, exam_type, max_mcqs, job_id)
        
        if not mcqs:
            job_status[job_id] = {
                "status": "completed",
                "error": "No relevant MCQs found",
                "mcqs_found": 0,
                "topic": topic,
                "exam_type": exam_type
            }
            return
        
        job_status[job_id]["stage"] = "generating_pdf" 
        job_status[job_id]["progress"] = 80
        job_status[job_id]["mcqs_found"] = len(mcqs)
        
        # Generate PDF
        pdf_path = generate_pdf(mcqs, topic, job_id, len(mcqs), 0, len(mcqs), pdf_format, exam_type)
        
        # Upload to S3 if available
        pdf_url = None
        if LAMBDA_S3_INTEGRATION:
            pdf_url = upload_pdf_to_s3_lambda(pdf_path, job_id, topic, exam_type)
        
        # Complete job
        job_status[job_id] = {
            "status": "completed",
            "progress": 100,
            "mcqs_found": len(mcqs),
            "topic": topic,
            "exam_type": exam_type,
            "pdf_path": pdf_path,
            "pdf_url": pdf_url,
            "completed_at": datetime.now().isoformat()
        }
        
        job_results[job_id] = {
            "mcqs": [mcq.dict() for mcq in mcqs],
            "pdf_path": pdf_path,
            "pdf_url": pdf_url
        }
        
    except Exception as e:
        job_status[job_id] = {
            "status": "error",
            "error": str(e),
            "progress": 0,
            "topic": topic,
            "exam_type": exam_type
        }
        logger.error(f"Error processing MCQ request {job_id}: {e}")

async def search_and_filter_mcqs(topic: str, exam_type: str, max_mcqs: int, job_id: str) -> List[MCQData]:
    """Search for MCQ URLs and scrape them"""
    try:
        job_status[job_id]["stage"] = "searching_urls"
        job_status[job_id]["progress"] = 20
        
        # Search for URLs using Google API
        urls = await api_key_manager.search_testbook_mcqs(topic, exam_type, max_mcqs * 2)
        
        if not urls:
            print(f"❌ No URLs found for topic: {topic}")
            print(f"💡 This could be due to Google API key restrictions or quota issues")
            print(f"🔧 Consider using direct URL scraping as fallback")
            
            # Fallback: Try with common Testbook URL patterns
            print(f"🔄 Attempting fallback URL patterns for topic: {topic}")
            fallback_urls = [
                f"https://testbook.com/question-answer/{topic.lower().replace(' ', '-')}-mcq-questions",
                f"https://testbook.com/question-answer/{topic.lower().replace(' ', '-')}-questions",
                f"https://testbook.com/question-answer/{topic.lower().replace(' ', '-')}-mcq",
                f"https://testbook.com/question-answer/{topic.lower().replace(' ', '-')}-quiz",
                f"https://testbook.com/question-answer/{topic.lower().replace(' ', '-')}-test"
            ]
            
            # Test fallback URLs
            for url in fallback_urls:
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                            if response.status == 200:
                                urls.append(url)
                                print(f"✅ Fallback URL found: {url}")
                except:
                    continue
            
            if not urls:
                print(f"❌ No fallback URLs found either")
                job_status[job_id]["status"] = "error"
                job_status[job_id]["error"] = f"No URLs found for topic: {topic}. Google API keys may have restrictions."
                return []
        
        job_status[job_id]["stage"] = "scraping_mcqs"
        job_status[job_id]["progress"] = 40
        job_status[job_id]["urls_found"] = len(urls)
        
        # Scrape MCQs concurrently
        mcqs = []
        semaphore = asyncio.Semaphore(10)  # Limit concurrent requests
        
        async def scrape_url(url):
            async with semaphore:
                return await http_scraper.scrape_testbook_page_concurrent(url, topic, exam_type)
        
        # Process URLs in batches
        tasks = [scrape_url(url) for url in urls[:max_mcqs * 2]]  # Get more than needed for filtering
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter successful results
        for result in results:
            if isinstance(result, dict) and result.get('is_relevant'):
                mcq = MCQData(**result)
                mcqs.append(mcq)
                
                if len(mcqs) >= max_mcqs:
                    break
        
        print(f"✅ Successfully scraped {len(mcqs)} relevant MCQs for '{topic}'")
        return mcqs[:max_mcqs]
        
    except Exception as e:
        logger.error(f"Error in search_and_filter_mcqs: {e}")
        return []

# Professional PDF styles and decorative elements
class GradientLine(Flowable):
    """Custom gradient line flowable for PDF decoration"""
    
    def __init__(self, width=6*inch, height=0.1*inch):
        self.width = width
        self.height = height
        Flowable.__init__(self)
        
    def draw(self):
        """Draw gradient line"""
        canvas = self.canv
        y = self.height / 2
        
        # Professional color palette
        primary_color = HexColor('#1a365d')
        accent_color = HexColor('#38b2ac')
        secondary_color = HexColor('#2b6cb0')
        
        # Main gradient line
        canvas.setStrokeColor(primary_color)
        canvas.setLineWidth(3)
        canvas.line(0, y, self.width * 0.3, y)
        
        canvas.setStrokeColor(accent_color)  
        canvas.line(self.width * 0.3, y, self.width * 0.7, y)
        
        canvas.setStrokeColor(secondary_color)
        canvas.line(self.width * 0.7, y, self.width, y)
        
        # Decorative diamonds
        canvas.setFillColor(accent_color)
        diamond_size = 4
        for x_pos in [self.width * 0.2, self.width * 0.5, self.width * 0.8]:
            canvas.circle(x_pos, y, diamond_size, fill=1)

# PDF Generation - Enhanced for Lambda
def generate_pdf(mcqs: List[MCQData], topic: str, job_id: str, relevant_mcqs: int, irrelevant_mcqs: int, total_links: int, pdf_format: str = "text", exam_type: str = "SSC") -> str:
    """Generate BEAUTIFUL PROFESSIONAL PDF - Lambda compatible"""
    temp_files_to_cleanup = []  # Track temporary files for cleanup
    
    try:
        pdf_dir = get_pdf_directory()
        
        filename = f"Testbook_MCQs_{topic.replace(' ', '_')}_{job_id}_{pdf_format}.pdf"
        filepath = pdf_dir / filename
        
        doc = SimpleDocTemplate(str(filepath), pagesize=A4, 
                              topMargin=0.6*inch, bottomMargin=0.6*inch,
                              leftMargin=0.6*inch, rightMargin=0.6*inch)
        
        styles = getSampleStyleSheet()
        
        # Premium Color Palette
        primary_color = HexColor('#1a365d')
        secondary_color = HexColor('#2b6cb0')
        accent_color = HexColor('#38b2ac')
        success_color = HexColor('#48bb78')
        text_color = HexColor('#2d3748')
        light_color = HexColor('#f7fafc')
        gradient_start = HexColor('#667eea')
        gold_color = HexColor('#d69e2e')
        
        # Typography Styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=36,
            spaceAfter=35,
            alignment=TA_CENTER,
            textColor=primary_color,
            fontName='Helvetica-Bold',
            borderWidth=3,
            borderColor=accent_color,
            borderPadding=20,
            backColor=light_color,
            leading=45
        )
        
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=styles['Normal'],
            fontSize=18,
            spaceAfter=30,
            alignment=TA_CENTER,
            textColor=secondary_color,
            fontName='Helvetica-Bold',
            borderWidth=2,
            borderColor=gold_color,
            borderPadding=12,
            backColor=white,
        )
        
        question_header_style = ParagraphStyle(
            'QuestionHeaderStyle',
            parent=styles['Normal'],
            fontSize=18,
            spaceAfter=20,
            fontName='Helvetica-Bold',
            textColor=white,
            borderWidth=3,
            borderColor=primary_color,
            borderPadding=15,
            backColor=gradient_start,
            alignment=TA_CENTER,
        )
        
        question_style = ParagraphStyle(
            'QuestionStyle',
            parent=styles['Normal'],
            fontSize=14,
            spaceAfter=18,
            textColor=text_color,
            fontName='Helvetica',
            borderWidth=2,
            borderColor=accent_color,
            borderPadding=15,
            backColor=light_color,
            leftIndent=20,
            rightIndent=20,
        )
        
        option_style = ParagraphStyle(
            'OptionStyle',
            parent=styles['Normal'],
            fontSize=13,
            spaceAfter=8,
            textColor=text_color,
            fontName='Helvetica',
            leftIndent=40,
            borderWidth=1,
            borderColor=HexColor('#e2e8f0'),
            borderPadding=8,
            backColor=HexColor('#f8fafc')
        )
        
        answer_style = ParagraphStyle(
            'AnswerStyle',
            parent=styles['Normal'],
            fontSize=13,
            spaceAfter=15,
            textColor=success_color,
            fontName='Helvetica-Bold',
            leftIndent=20,
            borderWidth=2,
            borderColor=success_color,
            borderPadding=12,
            backColor=HexColor('#f0fff4'),
        )
        
        exam_source_style = ParagraphStyle(
            'ExamSourceStyle',
            parent=styles['Normal'],
            fontSize=11,
            spaceAfter=12,
            textColor=HexColor('#718096'),
            fontName='Helvetica-Oblique',
            leftIndent=20,
            alignment=TA_RIGHT
        )
        
        stats_style = ParagraphStyle(
            'StatsStyle',
            parent=styles['Normal'],
            fontSize=14,
            spaceAfter=25,
            textColor=secondary_color,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            borderWidth=2,
            borderColor=accent_color,
            borderPadding=15,
            backColor=light_color,
        )
        
        # Build PDF content
        story = []
        
        # PROFESSIONAL HEADER SECTION
        # Main Title with decorative border
        story.append(Paragraph("🎯 TESTBOOK MCQ COLLECTION", title_style))
        story.append(GradientLine())  # Add decorative line
        story.append(Spacer(1, 20))
        
        # Topic and Exam Type with elegant styling
        topic_text = f"📚 Subject: <b>{topic.upper()}</b>"
        exam_text = f"📝 Exam Type: <b>{exam_type.upper()}</b>"
        
        story.append(Paragraph(topic_text, subtitle_style))
        story.append(Paragraph(exam_text, subtitle_style))
        story.append(Spacer(1, 15))
        
        # STATISTICS SECTION with professional layout
        stats_data = [
            ["📊 Generation Statistics", ""],
            ["Total MCQs Found:", str(len(mcqs))],
            ["Relevant MCQs:", str(relevant_mcqs)],
            ["Total Sources Searched:", str(total_links)],
            ["Generation Date:", datetime.now().strftime("%B %d, %Y at %I:%M %p")],
            ["Job ID:", job_id[:12] + "..."],
        ]
        
        stats_table = Table(stats_data, colWidths=[3.5*inch, 2.5*inch])
        stats_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), gradient_start),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 1, accent_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [light_color, white]),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('RIGHTPADDING', (0, 0), (-1, -1), 15),
            ('TOPPADDING', (0, 0), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ]))
        
        story.append(stats_table)
        story.append(Spacer(1, 30))
        story.append(GradientLine())
        story.append(PageBreak())
        
        # MCQ CONTENT SECTION
        for i, mcq in enumerate(mcqs, 1):
            try:
                # Question Header with numbering
                question_header = f"Question {i} of {len(mcqs)}"
                story.append(Paragraph(question_header, question_header_style))
                story.append(Spacer(1, 10))
                
                # Main Question with enhanced styling
                question_text = f"<b>Q{i}:</b> {mcq.question}"
                story.append(Paragraph(question_text, question_style))
                story.append(Spacer(1, 10))
                
                # Options with improved formatting
                if mcq.options:
                    option_labels = ['A', 'B', 'C', 'D', 'E']
                    for j, option in enumerate(mcq.options[:5]):
                        label = option_labels[j] if j < len(option_labels) else str(j+1)
                        option_text = f"<b>{label})</b> {option}"
                        story.append(Paragraph(option_text, option_style))
                        story.append(Spacer(1, 5))
                
                # Answer/Solution with distinctive styling
                if mcq.answer:
                    answer_text = f"✅ <b>Answer:</b> {mcq.answer}"
                    story.append(Spacer(1, 10))
                    story.append(Paragraph(answer_text, answer_style))
                
                # Exam Source Information with elegant presentation
                if mcq.combined_exam_source:
                    source_text = f"📋 <i>Source: {mcq.combined_exam_source}</i>"
                    story.append(Paragraph(source_text, exam_source_style))
                
                # Screenshot Integration for Image Format
                if pdf_format == "image" and mcq.screenshot:
                    try:
                        # Decode base64 screenshot
                        screenshot_data = base64.b64decode(mcq.screenshot)
                        
                        # Create temporary file for image
                        temp_image_path = pdf_dir / f"temp_screenshot_{i}_{job_id}.png"
                        temp_files_to_cleanup.append(temp_image_path)
                        
                        with open(temp_image_path, 'wb') as f:
                            f.write(screenshot_data)
                        
                        # Add image to PDF with proper sizing
                        img = ReportLabImage(str(temp_image_path), width=5*inch, height=3*inch)
                        story.append(Spacer(1, 15))
                        story.append(Paragraph("📸 <b>Visual Reference:</b>", 
                                             ParagraphStyle('ImageCaption', parent=styles['Normal'], 
                                                          fontSize=12, textColor=secondary_color, 
                                                          fontName='Helvetica-Bold')))
                        story.append(img)
                        
                        # Image dimensions info
                        try:
                            with PILImage.open(temp_image_path) as pil_img:
                                width, height = pil_img.size
                                dimensions_text = f"<i>Screenshot Dimensions: {width} × {height} pixels</i>"
                                story.append(Paragraph(dimensions_text, exam_source_style))
                        except Exception as img_error:
                            print(f"⚠️ Could not read image dimensions: {img_error}")
                            
                    except Exception as screenshot_error:
                        print(f"⚠️ Error adding screenshot for MCQ {i}: {screenshot_error}")
                        story.append(Paragraph("<i>📷 Screenshot unavailable</i>", exam_source_style))
                
                # URL Reference for traceability
                if mcq.url:
                    url_text = f"🔗 <i>Source URL: {mcq.url[:70]}...</i>"
                    story.append(Paragraph(url_text, exam_source_style))
                
                # Decorative separator between questions
                story.append(Spacer(1, 20))
                story.append(GradientLine())
                story.append(Spacer(1, 25))
                
                # Page break every 3 questions for better readability
                if i % 3 == 0 and i < len(mcqs):
                    story.append(PageBreak())
                    
            except Exception as mcq_error:
                print(f"⚠️ Error processing MCQ {i}: {mcq_error}")
                continue
        
        # PROFESSIONAL FOOTER SECTION
        story.append(PageBreak())
        story.append(GradientLine())
        story.append(Spacer(1, 20))
        
        footer_style = ParagraphStyle(
            'FooterStyle',
            parent=styles['Normal'],
            fontSize=12,
            textColor=HexColor('#4a5568'),
            fontName='Helvetica',
            alignment=TA_CENTER,
            spaceAfter=15
        )
        
        footer_content = [
            "📚 <b>Generated by Testbook MCQ Scraper</b>",
            f"🕒 Generated on: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            "⚡ Powered by Advanced Web Scraping & AI Technology",
            "🎯 For Educational and Practice Purposes Only",
        ]
        
        for footer_line in footer_content:
            story.append(Paragraph(footer_line, footer_style))
        
        # Build the PDF document
        print(f"📄 Building PDF with {len(story)} elements...")
        doc.build(story)
        
        # Cleanup temporary files
        for temp_file in temp_files_to_cleanup:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except Exception as cleanup_error:
                print(f"⚠️ Could not clean up temporary file {temp_file}: {cleanup_error}")
        
        print(f"✅ PDF generated successfully: {filepath}")
        print(f"📊 PDF contains {len(mcqs)} MCQs with professional formatting")
        
        return str(filepath)
        
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        
        # Cleanup temporary files on error
        for temp_file in temp_files_to_cleanup:
            try:
                if temp_file.exists():
                    temp_file.unlink()
            except:
                pass
        
        raise