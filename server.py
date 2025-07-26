from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
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
            
            # Extract exam source information
            exam_source_heading = ""
            exam_source_title = ""
            
            try:
                heading_elem = soup.select_one('div.pyp-heading')
                if heading_elem:
                    exam_source_heading = self._clean_text(heading_elem.get_text())
                
                title_elem = soup.select_one('div.pyp-title.line-ellipsis')
                if title_elem:
                    exam_source_title = self._clean_text(title_elem.get_text())
            except Exception as e:
                print(f"⚠️ Error extracting exam source: {e}")
            
            # Check exam type relevance
            if not self._is_exam_type_relevant(exam_source_heading, exam_source_title, exam_type):
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
    
    def _is_exam_type_relevant(self, heading: str, title: str, exam_type: str) -> bool:
        """Check if MCQ is relevant for the specified exam type"""
        exam_text = f"{heading} {title}".lower()
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
            
            timeout = aiohttp.ClientTimeout(total=30, connect=15)
            connector = aiohttp.TCPConnector(
                limit=20,
                limit_per_host=5,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                enable_cleanup_closed=True,
                # Add some randomization to connection behavior
                connector_timeout=random.uniform(10, 15)
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
                
                # Apply smart delay before request
                if attempt > 0:
                    await self.smart_delay('retry_delay', url)
                elif retry_count > 0:
                    await self.smart_delay('base_delay', url)
                else:
                    # Small delay for first attempts
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                
                print(f"🕵️ Stealth request attempt {attempt + 1} (retry #{retry_count}): {url}")
                
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
                        
                        # Exponential backoff for rate limits
                        backoff_delay = min(30 + (retry_count * 10), 120)  # Max 2 minutes
                        print(f"⏸️ Rate limit backoff: {backoff_delay}s")
                        await asyncio.sleep(backoff_delay)
                        
                        # Try with a different session
                        continue
                    
                    elif response.status in [403, 404, 502, 503]:
                        print(f"⚠️ HTTP {response.status} on attempt {attempt + 1}: {url}")
                        if attempt < max_attempts - 1:
                            await self.smart_delay('retry_delay', url)
                        continue
                    
                    else:
                        print(f"❌ Unexpected status {response.status}: {url}")
                        if attempt < max_attempts - 1:
                            await self.smart_delay('retry_delay', url)
                        continue
            
            except asyncio.TimeoutError:
                print(f"⏱️ Timeout on attempt {attempt + 1}: {url}")
                if attempt < max_attempts - 1:
                    await self.smart_delay('retry_delay', url)
                continue
                
            except Exception as e:
                print(f"💥 Error on attempt {attempt + 1} for {url}: {str(e)}")
                if attempt < max_attempts - 1:
                    await self.smart_delay('retry_delay', url)
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
                # STEALTH FEATURES
                '--disable-blink-features=AutomationControlled',
                '--disable-features=VizDisplayCompositor',
                '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--disable-web-security',
                '--disable-features=VizDisplayCompositor',
                '--disable-ipc-flooding-protection',
                '--disable-component-extensions-with-background-pages'
            ]
            
            self.browser = await launch(
                headless=True,
                args=browser_args,
                executablePath='/usr/bin/chromium',  # Use system-installed ARM64 compatible Chromium
                autoClose=False,
                dumpio=False,
                devtools=False
            )
            
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
            print(f"📸 Capturing stealth screenshot with 67% zoom for URL: {url}")
            
            # Create new page with stealth settings
            page = await self.browser.newPage()
            
            # STEALTH CONFIGURATION
            # Set random realistic viewport
            viewport_options = [
                {'width': 1366, 'height': 768},  # Common laptop
                {'width': 1920, 'height': 1080}, # Common desktop
                {'width': 1440, 'height': 900},  # MacBook Air
                {'width': 1280, 'height': 720}   # HD
            ]
            chosen_viewport = random.choice(viewport_options)
            await page.setViewport(chosen_viewport)
            
            # Set random user agent
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
            await page.setUserAgent(random.choice(user_agents))
            
            # Add realistic headers
            await page.setExtraHTTPHeaders({
                'Accept-Language': 'en-US,en;q=0.9,hi;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })
            
            # Hide webdriver traces
            await page.evaluateOnNewDocument('''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                });
                
                // Remove Chrome automation indicators
                window.chrome = {
                    runtime: {},
                };
                
                // Mock permissions
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Reflect.get(Notification, 'permission') || 'granted' }) :
                        originalQuery(parameters)
                );
                
                // Mock plugins
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                });
                
                // Mock languages
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en', 'hi'],
                });
            ''')
            
            # Random delay before navigation (human-like behavior)
            await asyncio.sleep(random.uniform(0.5, 2.0))
            
            # Navigate with extended timeout for rate-limited responses
            try:
                await asyncio.wait_for(
                    page.goto(url, {
                        'waitUntil': 'domcontentloaded', 
                        'timeout': 25000,
                        'referer': 'https://www.google.com/'  # Stealth referrer
                    }),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                print(f"⏱️ Navigation timeout for {url}")
                return None
            
            # Wait for page settling (human-like behavior)
            await page.waitFor(random.randint(1000, 2000))
            
            # Check for rate limiting or blocking
            page_title = await page.title()
            page_url = page.url
            
            if 'blocked' in page_title.lower() or 'access denied' in page_title.lower():
                print(f"🚫 Page appears to be blocked: {url}")
                return None
            
            # CRITICAL: Set page zoom to 67% as in reference server.py
            await page.evaluate("document.body.style.zoom = '0.67'")
            await page.waitFor(500)
            
            # Scroll to ensure we can see the complete MCQ content
            await page.evaluate("window.scrollTo(0, 0)")
            await page.waitFor(300)
            
            # Quick relevance check
            question_found = False
            try:
                question_element = await page.querySelector('h1.questionBody, div.questionBody, h1, h2, h3')
                if question_element:
                    question_text = await page.evaluate('(element) => element.innerText', question_element)
                    if question_text and len(question_text.strip()) > 10:
                        question_found = True
                        print(f"✅ Found question content")
            except Exception as e:
                print(f"⚠️ Error checking question content: {e}")
            
            if not question_found:
                print(f"❌ No valid question content found on {url}")
                return None
            
            # Get page dimensions after zoom (same as reference server.py)
            page_height = await page.evaluate("document.body.scrollHeight")
            page_width = await page.evaluate("document.body.scrollWidth")
            viewport_height = await page.evaluate("window.innerHeight")
            
            print(f"📏 Page dimensions with 67% zoom: {page_width}x{page_height}, viewport: {viewport_height}")
            
            # Calculate central area cropping (same as reference server.py)
            crop_left = 100
            crop_top = 100
            crop_right = 430
            
            screenshot_x = crop_left
            screenshot_y = crop_top
            screenshot_width = min(chosen_viewport['width'] - crop_left - crop_right, page_width - screenshot_x)
            
            # Calculate height to include question, options, AND answer section
            base_height = 600
            answer_section_height = 600
            screenshot_height = base_height + answer_section_height
            
            max_height = 1400
            if screenshot_height > max_height:
                screenshot_height = max_height
            
            # Ensure screenshot region is within page bounds
            screenshot_height = min(screenshot_height, page_height - screenshot_y)
            
            screenshot_region = {
                'x': screenshot_x,
                'y': screenshot_y,
                'width': screenshot_width,
                'height': screenshot_height
            }
            
            print(f"🎯 MCQ screenshot region (67% zoom): x={screenshot_x}, y={screenshot_y}, w={screenshot_width}, h={screenshot_height}")
            
            # Add small random delay before screenshot (human-like)
            await asyncio.sleep(random.uniform(0.3, 0.8))
            
            # Capture the screenshot with maximum quality settings
            try:
                screenshot = await asyncio.wait_for(
                    page.screenshot(
                        clip=screenshot_region,
                        type='png',
                        omitBackground=False
                    ),
                    timeout=20.0
                )
                
                print(f"✅ Stealth MCQ screenshot captured: {screenshot_width}x{screenshot_height}px")
                self.screenshot_stats["screenshots_captured"] += 1
                return screenshot
                
            except asyncio.TimeoutError:
                print(f"⏱️ Screenshot timeout for {url}")
                self.screenshot_stats["screenshot_failures"] += 1
                return None
            
        except Exception as e:
            print(f"❌ Error capturing stealth screenshot for {url}: {str(e)}")
            self.screenshot_stats["screenshot_failures"] += 1
            return None
        finally:
            if page:
                try:
                    await page.close()
                except:
                    pass
    
    async def scrape_with_screenshot(self, url: str, topic: str, exam_type: str = "SSC") -> Optional[dict]:
        """
        Scrape testbook page with screenshot capture and relevancy checks
        Adapted from reference server.py
        """
        try:
            print(f"🔍 Processing URL with screenshot: {url}")
            
            # First do HTTP scraping for text content and relevance checks
            mcq_data = await http_scraper._scrape_testbook_page_internal(url, topic, exam_type)
            
            if not mcq_data or not mcq_data.get('is_relevant'):
                print(f"❌ MCQ not relevant or failed text extraction for {url}")
                return None
            
            # If relevant, capture screenshot
            screenshot = await self.capture_screenshot(url, topic)
            
            if screenshot:
                # Convert screenshot to base64 for storage
                screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
                mcq_data['screenshot'] = screenshot_base64
                print(f"✅ Successfully captured and processed screenshot for {url}")
            else:
                print(f"⚠️ Failed to capture screenshot for {url}, keeping text data")
            
            return mcq_data
            
        except Exception as e:
            print(f"❌ Error in screenshot scraping for {url}: {str(e)}")
            return None
    
    async def close(self):
        """Close Puppeteer browser"""
        if self.browser:
            try:
                await self.browser.close()
                print("✅ Puppeteer browser closed")
            except Exception as e:
                print(f"⚠️ Error closing Puppeteer browser: {e}")
            finally:
                self.browser = None
                self.is_initialized = False
    
    def get_stats(self) -> dict:
        """Get screenshot statistics"""
        total_attempts = self.screenshot_stats["screenshots_captured"] + self.screenshot_stats["screenshot_failures"]
        success_rate = (self.screenshot_stats["screenshots_captured"] / max(total_attempts, 1)) * 100
        
        return {
            "puppeteer_available": PUPPETEER_AVAILABLE,
            "initialized": self.is_initialized,
            "screenshot_stats": self.screenshot_stats,
            "success_rate": success_rate
        }

# Global Puppeteer screenshot manager
screenshot_manager = PuppeteerScreenshotManager()

# PERSISTENT JOB STORAGE - Lambda Compatible
class PersistentJobStorage:
    """Persistent storage for job progress - Lambda compatible"""
    
    def __init__(self):
        self.storage_file = "/tmp/job_progress.pkl"
        self.jobs = {}
        self.load_jobs()
    
    def load_jobs(self):
        """Load jobs from persistent storage"""
        try:
            if os.path.exists(self.storage_file):
                with open(self.storage_file, 'rb') as f:
                    self.jobs = pickle.load(f)
                print(f"📂 Loaded {len(self.jobs)} jobs from persistent storage")
            else:
                self.jobs = {}
                print("📂 No persistent storage found, starting fresh")
        except Exception as e:
            print(f"⚠️ Error loading jobs from storage: {e}")
            self.jobs = {}
    
    def save_jobs(self):
        """Save jobs to persistent storage"""
        try:
            with open(self.storage_file, 'wb') as f:
                pickle.dump(self.jobs, f)
        except Exception as e:
            print(f"⚠️ Error saving jobs to storage: {e}")
    
    def update_job(self, job_id: str, status: str, progress: str, **kwargs):
        """Update job progress with automatic persistence"""
        try:
            if job_id not in self.jobs:
                self.jobs[job_id] = {
                    "job_id": job_id,
                    "status": status,
                    "progress": progress,
                    "total_links": 0,
                    "processed_links": 0,
                    "mcqs_found": 0,
                    "pdf_url": None,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat()
                }
            
            self.jobs[job_id].update({
                "status": status,
                "progress": progress,
                "updated_at": datetime.now().isoformat(),
                **kwargs
            })
            
            self.save_jobs()
            print(f"📊 Job {job_id}: {status} - {progress}")
            
        except Exception as e:
            print(f"⚠️ Error updating job progress: {e}")
    
    def get_job(self, job_id: str) -> Optional[dict]:
        """Get job status"""
        return self.jobs.get(job_id)
    
    def cleanup_old_jobs(self, hours: int = 24):
        """Clean up jobs older than specified hours"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=hours)
            jobs_to_remove = []
            
            for job_id, job_data in self.jobs.items():
                try:
                    updated_at = datetime.fromisoformat(job_data.get('updated_at', ''))
                    if updated_at < cutoff_time:
                        jobs_to_remove.append(job_id)
                except:
                    jobs_to_remove.append(job_id)
            
            for job_id in jobs_to_remove:
                del self.jobs[job_id]
            
            if jobs_to_remove:
                self.save_jobs()
                print(f"🗑️ Cleaned up {len(jobs_to_remove)} old jobs")
                
        except Exception as e:
            print(f"⚠️ Error cleaning up old jobs: {e}")

# Global persistent job storage
persistent_storage = PersistentJobStorage()

# API KEY MANAGEMENT - Enhanced for Lambda
class APIKeyManager:
    """API Key management for Google Custom Search - Lambda optimized"""
    
    def __init__(self):
        self.api_keys = []
        self.current_key_index = 0
        self.exhausted_keys = set()
        self._load_api_keys()
    
    def _load_api_keys(self):
        """Load API keys from environment"""
        try:
            api_key_pool = os.environ.get('API_KEY_POOL', '')
            if api_key_pool:
                self.api_keys.extend([key.strip() for key in api_key_pool.split(',') if key.strip()])
            
            google_api_key = os.environ.get('GOOGLE_API_KEY', '')
            if google_api_key:
                self.api_keys.append(google_api_key.strip())
            
            self.api_keys = list(set(self.api_keys))
            print(f"🔑 Initialized API Key Manager with {len(self.api_keys)} keys")
            
        except Exception as e:
            print(f"⚠️ Error loading API keys: {e}")
            self.api_keys = []
    
    def get_current_key(self) -> str:
        """Get current API key"""
        if not self.api_keys:
            raise Exception("No API keys available")
        return self.api_keys[self.current_key_index]
    
    def rotate_key(self) -> Optional[str]:
        """Rotate to next API key"""
        if not self.api_keys:
            return None
        
        current_key = self.api_keys[self.current_key_index]
        self.exhausted_keys.add(current_key)
        
        for i in range(len(self.api_keys)):
            key = self.api_keys[i]
            if key not in self.exhausted_keys:
                self.current_key_index = i
                return key
        
        return None
    
    def is_quota_error(self, error_message: str) -> bool:
        """Check if error is quota related"""
        quota_indicators = [
            "quota exceeded", "quotaExceeded", "rateLimitExceeded",
            "userRateLimitExceeded", "dailyLimitExceeded", "Too Many Requests"
        ]
        return any(indicator.lower() in error_message.lower() for indicator in quota_indicators)
    
    def get_remaining_keys(self) -> int:
        """Get number of remaining keys"""
        return len(self.api_keys) - len(self.exhausted_keys)

# Global API key manager
api_key_manager = APIKeyManager()

# Environment variables
SEARCH_ENGINE_ID = os.environ.get('SEARCH_ENGINE_ID', '2701a7d64a00d47fd')

# Generated PDFs storage
generated_pdfs = {}

# Data Models
class SearchRequest(BaseModel):
    topic: str
    exam_type: str = "SSC"
    pdf_format: str = "text"

class MCQData(BaseModel):
    question: str
    options: List[str]
    answer: str
    exam_source_heading: str = ""
    exam_source_title: str = ""
    is_relevant: bool = True
    screenshot: Optional[str] = None

class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: str
    total_links: Optional[int] = 0
    processed_links: Optional[int] = 0
    mcqs_found: Optional[int] = 0
    pdf_url: Optional[str] = None

# Lambda-compatible utility functions
def update_job_progress(job_id: str, status: str, progress: str, **kwargs):
    """Update job progress using persistent storage"""
    try:
        # Clean unicode characters from progress messages
        clean_progress = progress.encode('utf-8', errors='ignore').decode('utf-8')
        
        # Replace emoji characters with text equivalents
        emoji_replacements = {
            '🚀': '[STARTING]',
            '📊': '[STATUS]',
            '✅': '[SUCCESS]',
            '❌': '[ERROR]',
            '⏱️': '[TIMEOUT]',
            '⚠️': '[WARNING]',
            '🎯': '[PROCESSING]',
            '📸': '[SCREENSHOT]',
            '🖼️': '[IMAGE]',
            '📄': '[PDF]',
            '🔄': '[RETRY]',
            '📍': '[FOUND]',
            '📏': '[DIMENSIONS]',
            '🔍': '[SEARCH]',
            '🌐': '[WEB]',
            '⭐': '[COMPLETE]',
            '🎉': '[DONE]',
            '💡': '[INFO]',
            '🔧': '[PROCESSING]',
            '🚩': '[FLAG]',
            '📈': '[PROGRESS]',
            '🎪': '[MCQ]',
            '💾': '[SAVED]'
        }
        
        for emoji, replacement in emoji_replacements.items():
            clean_progress = clean_progress.replace(emoji, replacement)
        
        persistent_storage.update_job(job_id, status, clean_progress, **kwargs)
    except Exception as e:
        print(f"[ERROR] Error updating job progress: {e}")

# Google Custom Search - Lambda optimized
async def search_google_custom(topic: str, exam_type: str = "SSC") -> List[str]:
    """Search Google Custom Search API - Lambda optimized"""
    if exam_type.upper() == "BPSC":
        query = f'{topic} Testbook [Solved] "This question was previously asked in" ("BPSC" OR "Bihar Public Service Commission" OR "BPSC Combined" OR "BPSC Prelims")'
    else:
        query = f'{topic} Testbook [Solved] "This question was previously asked in" "SSC"'
    
    base_url = "https://www.googleapis.com/customsearch/v1"
    headers = {
        "Referer": "https://testbook.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    all_testbook_links = []
    start_index = 1
    max_results = 40
    
    try:
        # Use aiohttp for async requests
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while start_index <= max_results:
                current_key = api_key_manager.get_current_key()
                
                params = {
                    "key": current_key,
                    "cx": SEARCH_ENGINE_ID,
                    "q": query,
                    "num": 10,
                    "start": start_index
                }
                
                print(f"🔍 Fetching results {start_index}-{start_index+9} for topic: {topic}")
                print(f"🔑 Using key: {current_key[:20]}... (Remaining: {api_key_manager.get_remaining_keys()})")
                
                async with session.get(base_url, params=params, headers=headers) as response:
                    if response.status == 429 or (response.status == 403 and "quota" in (await response.text()).lower()):
                        print(f"⚠️ Quota exceeded for current key. Attempting rotation...")
                        
                        next_key = api_key_manager.rotate_key()
                        if next_key is None:
                            print("❌ All API keys exhausted!")
                            raise Exception("All Servers are exhausted due to intense use")
                        
                        continue
                    
                    response.raise_for_status()
                    data = await response.json()
                    
                    if "items" not in data or len(data["items"]) == 0:
                        print(f"No more results found after {start_index-1} results")
                        break
                    
                    batch_links = []
                    for item in data["items"]:
                        link = item.get("link", "")
                        if "testbook.com" in link:
                            batch_links.append(link)
                    
                    all_testbook_links.extend(batch_links)
                    print(f"✅ Found {len(batch_links)} Testbook links in this batch. Total so far: {len(all_testbook_links)}")
                    
                    if len(data["items"]) < 10:
                        print(f"Reached end of results with {len(data['items'])} items in last batch")
                        break
                    
                    start_index += 10
                    await asyncio.sleep(0.5)
        
        print(f"✅ Total Testbook links found: {len(all_testbook_links)}")
        return all_testbook_links
        
    except Exception as e:
        print(f"❌ Error searching Google: {e}")
        if "All Servers are exhausted due to intense use" in str(e):
            raise e
        return []

# CUSTOM VISUAL ELEMENTS - Beautiful Graphics (from original code)
class GradientHeader(Flowable):
    """Custom gradient header with beautiful visual design"""
    def __init__(self, width, height, title, subtitle=""):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.title = title
        self.subtitle = subtitle
        
    def draw(self):
        canvas = self.canv
        
        # Define colors
        gradient_start = HexColor('#667eea')
        white = HexColor('#ffffff')
        
        # Gradient background rectangle
        canvas.setFillColor(gradient_start)
        canvas.rect(0, 0, self.width, self.height, fill=1, stroke=0)
        
        # Overlay with subtle gradient effect
        canvas.setFillColorRGB(0.4, 0.47, 0.91, alpha=0.8)
        canvas.rect(0, 0, self.width, self.height * 0.6, fill=1, stroke=0)
        
        # Decorative circles
        canvas.setFillColor(white)
        canvas.setFillColorRGB(1, 1, 1, alpha=0.2)
        canvas.circle(self.width - 30, self.height - 20, 15, fill=1)
        canvas.circle(self.width - 70, self.height - 35, 10, fill=1)
        canvas.circle(self.width - 110, self.height - 15, 8, fill=1)
        
        # Title text
        canvas.setFillColor(white)
        canvas.setFont("Helvetica-Bold", 20)
        text_width = canvas.stringWidth(self.title, "Helvetica-Bold", 20)
        canvas.drawString((self.width - text_width) / 2, self.height - 35, self.title)
        
        # Subtitle if provided
        if self.subtitle:
            canvas.setFont("Helvetica", 12)
            sub_width = canvas.stringWidth(self.subtitle, "Helvetica", 12)
            canvas.drawString((self.width - sub_width) / 2, self.height - 55, self.subtitle)

class DecorativeSeparator(Flowable):
    """Beautiful decorative separator line"""
    def __init__(self, width, height=0.1*inch):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        
    def draw(self):
        canvas = self.canv
        y = self.height / 2
        
        # Define colors
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
def generate_pdf(mcqs: List[MCQData], topic: str, job_id: str, relevant_mcqs: int, irrelevant_mcqs: int, total_links: int, pdf_format: str = "text") -> str:
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
            spaceAfter=12,
            textColor=text_color,
            fontName='Helvetica',
            leftIndent=30,
            rightIndent=20,
            borderWidth=1,
            borderColor=secondary_color,
            borderPadding=10,
            backColor=white,
        )
        
        answer_style = ParagraphStyle(
            'AnswerStyle',
            parent=styles['Normal'],
            fontSize=13,
            spaceAfter=15,
            textColor=primary_color,
            fontName='Helvetica',
            borderWidth=2,
            borderColor=success_color,
            borderPadding=15,
            backColor=light_color,
            leftIndent=20,
            rightIndent=20,
        )
        
        # Story Elements
        story = []
        
        # Cover Page
        story.append(DecorativeSeparator(doc.width, 0.2*inch))
        story.append(Spacer(1, 0.3*inch))
        
        story.append(Paragraph("PREMIUM MCQ COLLECTION", title_style))
        story.append(Spacer(1, 0.2*inch))
        
        story.append(Paragraph(f"Subject: {topic.upper()}", subtitle_style))
        story.append(Spacer(1, 0.3*inch))
        
        story.append(DecorativeSeparator(doc.width, 0.15*inch))
        story.append(Spacer(1, 0.4*inch))
        
        # Statistics Table
        stats_data = [
            ['COLLECTION ANALYTICS', ''],
            ['Search Topic', f'{topic}'],
            ['Total Quality Questions', f'{len(mcqs)}'],
            ['Smart Filtering Applied', 'Ultra-Premium Topic-based'],
            ['Generated On', f'{datetime.now().strftime("%B %d, %Y at %I:%M %p")}'],
            ['Authoritative Source', 'Testbook.com (Premium Grade)'],
            ['Quality Assurance', 'Professional Excellence'],
            ['Processing Method', 'Lambda HTTP Enhanced']
        ]
        
        stats_table = Table(stats_data, colWidths=[3*inch, 2.5*inch])
        
        stats_table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), primary_color),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 14),
            ('LEFTPADDING', (0, 0), (-1, -1), 15),
            ('RIGHTPADDING', (0, 0), (-1, -1), 15),
            ('TOPPADDING', (0, 0), (-1, 0), 15),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 15),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('TOPPADDING', (0, 1), (-1, -1), 12),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 12),
            ('BACKGROUND', (0, 1), (-1, 1), light_color),
            ('BACKGROUND', (0, 2), (-1, 2), white),
            ('BACKGROUND', (0, 3), (-1, 3), light_color),
            ('BACKGROUND', (0, 4), (-1, 4), white),
            ('BACKGROUND', (0, 5), (-1, 5), light_color),
            ('BACKGROUND', (0, 6), (-1, 6), white),
            ('BACKGROUND', (0, 7), (-1, 7), light_color),
            ('GRID', (0, 0), (-1, -1), 2, accent_color),
            ('LINEBELOW', (0, 0), (-1, 0), 3, secondary_color),
            ('LINEBEFORE', (0, 0), (0, -1), 3, accent_color),
            ('LINEAFTER', (-1, 0), (-1, -1), 3, accent_color),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TEXTCOLOR', (0, 1), (-1, -1), text_color),
            ('TEXTCOLOR', (1, 1), (-1, -1), primary_color),
        ]
        
        stats_table.setStyle(TableStyle(stats_table_style))
        
        story.append(stats_table)
        story.append(Spacer(1, 0.4*inch))
        story.append(DecorativeSeparator(doc.width, 0.15*inch))
        story.append(PageBreak())
        
        # MCQ Content
        for i, mcq in enumerate(mcqs, 1):
            story.append(Paragraph(f"QUESTION {i} OF {len(mcqs)}", question_header_style))
            story.append(Spacer(1, 0.15*inch))
            
            # Exam source
            exam_info = ""
            if mcq.exam_source_heading:
                exam_info = mcq.exam_source_heading
            elif mcq.exam_source_title:
                exam_info = mcq.exam_source_title
            else:
                exam_info = f"{topic} Practice Question"
            
            story.append(Paragraph(f"<i>{exam_info}</i>", 
                ParagraphStyle('ExamInfo', parent=styles['Normal'], 
                    fontSize=11, textColor=secondary_color, alignment=TA_CENTER, 
                    fontName='Helvetica-Oblique', spaceAfter=15,
                    borderWidth=1, borderColor=accent_color, borderPadding=8, 
                    backColor=light_color)))
            
            story.append(Spacer(1, 0.1*inch))
            
            # Handle PDF format - IMAGE vs TEXT
            if pdf_format == "image":
                # Try to add screenshot if available
                screenshot_added = False
                
                # Check if MCQ has screenshot data
                if hasattr(mcq, 'screenshot') and mcq.screenshot:
                    try:
                        # Decode base64 screenshot
                        screenshot_data = base64.b64decode(mcq.screenshot)
                        
                        # Create image from screenshot data
                        # Save image temporarily and create ReportLab image
                        import tempfile
                        import os
                        
                        # Create a temporary file for the image
                        temp_fd, temp_image_path = tempfile.mkstemp(suffix='.png')
                        temp_files_to_cleanup.append(temp_image_path)
                        
                        # Write the image data to the temporary file
                        with os.fdopen(temp_fd, 'wb') as temp_file:
                            temp_file.write(screenshot_data)
                        
                        # Add image to story with proper sizing
                        img_width = 5*inch
                        img_height = 3*inch
                        
                        screenshot_img = ReportLabImage(temp_image_path, width=img_width, height=img_height)
                        story.append(screenshot_img)
                        story.append(Spacer(1, 0.2*inch))
                        
                        screenshot_added = True
                        
                    except Exception as e:
                        print(f"⚠️ Error adding screenshot for MCQ {i}: {e}")
                        # Fall back to text format
                        screenshot_added = False
                
                # If no screenshot or screenshot failed, fall back to text format
                if not screenshot_added:
                    # Add a note about image format
                    story.append(Paragraph(f"<b>IMAGE FORMAT:</b> Original webpage screenshot", 
                        ParagraphStyle('ImageNote', parent=styles['Normal'], 
                            fontSize=10, textColor=accent_color, alignment=TA_CENTER, 
                            fontName='Helvetica-Oblique', spaceAfter=10,
                            borderWidth=1, borderColor=accent_color, borderPadding=5, 
                            backColor=light_color)))
                    
                    # Add the text content as well for image format
                    if mcq.question:
                        question_text = mcq.question.replace('\n', '<br/>')
                        story.append(Paragraph(f"<b>QUESTION:</b><br/><br/>{question_text}", question_style))
                    
                    story.append(Spacer(1, 0.15*inch))
                    
                    # Options
                    if mcq.options:
                        for j, option in enumerate(mcq.options, 1):
                            option_letter = chr(64 + j)
                            option_text = option.replace('\n', '<br/>')
                            story.append(Paragraph(f"<b>{option_letter})</b> {option_text}", option_style))
                    
                    story.append(Spacer(1, 0.15*inch))
                    
                    # Answer
                    if mcq.answer:
                        answer_text = mcq.answer.replace('\n', '<br/>')
                        story.append(Paragraph(f"<b>CORRECT ANSWER & EXPLANATION:</b><br/><br/>{answer_text}", answer_style))
            
            else:
                # TEXT FORMAT (original logic)
                # Question
                if mcq.question:
                    question_text = mcq.question.replace('\n', '<br/>')
                    story.append(Paragraph(f"<b>QUESTION:</b><br/><br/>{question_text}", question_style))
                
                story.append(Spacer(1, 0.15*inch))
                
                # Options
                if mcq.options:
                    for j, option in enumerate(mcq.options, 1):
                        option_letter = chr(64 + j)
                        option_text = option.replace('\n', '<br/>')
                        story.append(Paragraph(f"<b>{option_letter})</b> {option_text}", option_style))
                
                story.append(Spacer(1, 0.15*inch))
                
                # Answer
                if mcq.answer:
                    answer_text = mcq.answer.replace('\n', '<br/>')
                    story.append(Paragraph(f"<b>CORRECT ANSWER & EXPLANATION:</b><br/><br/>{answer_text}", answer_style))
            
            # Separator between questions
            if i < len(mcqs):
                story.append(Spacer(1, 0.3*inch))
                story.append(DecorativeSeparator(doc.width, 0.1*inch))
                story.append(PageBreak())
        
        # Footer
        story.append(Spacer(1, 0.5*inch))
        story.append(DecorativeSeparator(doc.width, 0.2*inch))
        story.append(Spacer(1, 0.2*inch))
        
        story.append(Paragraph("<b>PREMIUM COLLECTION COMPLETE</b>", 
            ParagraphStyle('FooterTitle', parent=styles['Normal'], 
                fontSize=16, alignment=TA_CENTER, textColor=gold_color,
                fontName='Helvetica-Bold', spaceAfter=15)))
        
        story.append(Paragraph(f"Generated by HEMANT SINGH", 
            ParagraphStyle('CreditSubtext', parent=styles['Normal'], 
                fontSize=14, alignment=TA_CENTER, textColor=accent_color,
                fontName='Helvetica-Oblique')))
        
        story.append(Spacer(1, 0.5*inch))
        story.append(DecorativeSeparator(doc.width, 0.2*inch))
        
        # Build PDF
        doc.build(story)
        
        # Clean up temporary files
        for temp_file in temp_files_to_cleanup:
            try:
                import os
                os.unlink(temp_file)
            except:
                pass
        
        print(f"✅ BEAUTIFUL PROFESSIONAL PDF generated successfully: {filename} with {len(mcqs)} MCQs")
        return filename
        
    except Exception as e:
        # Clean up temporary files in case of error
        for temp_file in temp_files_to_cleanup:
            try:
                import os
                os.unlink(temp_file)
            except:
                pass
        
        print(f"❌ Error generating beautiful PDF: {e}")
        raise

# Enhanced concurrent processing for Lambda
async def process_mcq_extraction(job_id: str, topic: str, exam_type: str = "SSC", pdf_format: str = "text"):
    """Enhanced MCQ extraction with 30 concurrent processes"""
    try:
        update_job_progress(job_id, "running", f"[SEARCH] Searching for {exam_type} '{topic}' results with ultra-smart filtering...")
        
        # Search for links
        links = await search_google_custom(topic, exam_type)
        
        if not links:
            update_job_progress(job_id, "completed", f"[ERROR] No {exam_type} results found for '{topic}'. Please try another topic.", 
                              total_links=0, processed_links=0, mcqs_found=0)
            return
        
        update_job_progress(job_id, "running", f"[SUCCESS] Found {len(links)} {exam_type} links. Starting concurrent extraction with 30 processes...", 
                          total_links=len(links))
        
        # Process with concurrent scraping
        await process_concurrent_extraction(job_id, topic, exam_type, links, pdf_format)
        
    except Exception as e:
        error_message = str(e)
        print(f"❌ Critical error in process_mcq_extraction: {e}")
        update_job_progress(job_id, "error", f"[ERROR] Error: {error_message}")

async def process_concurrent_extraction(job_id: str, topic: str, exam_type: str, links: List[str], pdf_format: str):
    """
    ENHANCED: Process extraction with intelligent anti-rate-limiting stealth system
    Ensures no MCQ is left behind through aggressive retry mechanisms
    """
    scraping_method = "text"  # Default value
    
    try:
        # Initialize stealth manager
        await stealth_manager.initialize_session_pool(pool_size=8)  # More sessions for rotation
        
        # Choose scraping method based on PDF format
        if pdf_format == "image" and PUPPETEER_AVAILABLE:
            print(f"🚀 Using Stealth Puppeteer screenshot scraping for image format")
            await screenshot_manager.initialize()
            scraping_method = "screenshot"
        else:
            print(f"🚀 Using Stealth HTTP text scraping for text format")
            await http_scraper.initialize()
            scraping_method = "text"
        
        print(f"🕵️ STEALTH MODE ACTIVATED: Processing {len(links)} links with anti-rate-limiting")
        
        # PHASE 1: Initial processing with stealth features
        all_results = []
        failed_urls = []
        
        # Use smaller batches to avoid overwhelming servers
        batch_size = 8 if scraping_method == "screenshot" else 12
        
        for i in range(0, len(links), batch_size):
            batch_links = links[i:i+batch_size]
            print(f"🔄 Processing batch {i//batch_size + 1}/{(len(links)-1)//batch_size + 1} with {len(batch_links)} links")
            
            # Create tasks for this batch
            tasks = []
            for url in batch_links:
                if scraping_method == "screenshot":
                    # Enhanced screenshot scraping with stealth
                    task = screenshot_manager.scrape_with_screenshot(url, topic, exam_type)
                else:
                    # Enhanced HTTP scraping with stealth requests  
                    task = http_scraper.scrape_testbook_page_with_stealth(url, topic, exam_type)
                tasks.append((url, task))
            
            # Process batch with individual error handling
            for url, task in tasks:
                try:
                    result = await task
                    if result and result.get('is_relevant'):
                        all_results.append(result)
                        print(f"✅ SUCCESS: {url}")
                    else:
                        failed_urls.append(url)
                        print(f"⚠️ FAILED or IRRELEVANT: {url}")
                except Exception as e:
                    failed_urls.append(url)
                    print(f"❌ ERROR processing {url}: {str(e)}")
                
                # Anti-rate-limiting delay between requests
                await asyncio.sleep(random.uniform(1.5, 3.0))
            
            # Update progress after each batch
            processed_count = min(i + batch_size, len(links))
            update_job_progress(job_id, "running", 
                              f"[BATCH {i//batch_size + 1}] {scraping_method.upper()} - Processed {processed_count}/{len(links)} links - Found {len(all_results)} MCQs - {len(failed_urls)} failed", 
                              processed_links=processed_count, mcqs_found=len(all_results))
            
            # Longer delay between batches to avoid rate limiting
            if i + batch_size < len(links):
                batch_delay = random.uniform(5, 10)
                print(f"⏸️ Inter-batch delay: {batch_delay:.2f}s")
                await asyncio.sleep(batch_delay)
        
        print(f"📊 PHASE 1 COMPLETE: {len(all_results)} MCQs found, {len(failed_urls)} failed")
        
        # PHASE 2: Aggressive retry system for failed URLs
        if failed_urls:
            print(f"🔄 PHASE 2: Aggressive retry for {len(failed_urls)} failed URLs...")
            update_job_progress(job_id, "running", 
                              f"[RETRY PHASE] Attempting to recover {len(failed_urls)} failed URLs with maximum stealth...", 
                              processed_links=len(links), mcqs_found=len(all_results))
            
            # Add failed URLs to stealth manager retry queue
            stealth_manager.retry_queue.extend(failed_urls)
            
            # Process with maximum stealth and patience
            retry_results = []
            for i, url in enumerate(failed_urls):
                try:
                    print(f"🕵️ STEALTH RETRY {i+1}/{len(failed_urls)}: {url}")
                    
                    # Ultra-conservative delay
                    await asyncio.sleep(random.uniform(8, 15))
                    
                    # Try with stealth HTTP first
                    success, html_content, status = await stealth_manager.stealth_request(url, max_attempts=3)
                    
                    if success and html_content:
                        soup = BeautifulSoup(html_content, 'html.parser')
                        
                        if scraping_method == "screenshot":
                            # For screenshot method, just extract text data for now
                            mcq_data = await http_scraper._extract_mcq_from_soup(soup, url, topic, exam_type)
                            
                            # Try to get screenshot if text extraction worked
                            if mcq_data and mcq_data.get('is_relevant'):
                                screenshot = await screenshot_manager.capture_screenshot(url, topic)
                                if screenshot:
                                    screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
                                    mcq_data['screenshot'] = screenshot_base64
                        else:
                            mcq_data = await http_scraper._extract_mcq_from_soup(soup, url, topic, exam_type)
                        
                        if mcq_data and mcq_data.get('is_relevant'):
                            retry_results.append(mcq_data)
                            print(f"🎉 RETRY SUCCESS: {url}")
                        else:
                            print(f"⚠️ RETRY - Not relevant: {url}")
                    else:
                        print(f"❌ RETRY FAILED: {url} (status: {status})")
                
                except Exception as e:
                    print(f"💥 RETRY ERROR: {url} - {str(e)}")
                
                # Update retry progress
                if (i + 1) % 5 == 0:
                    update_job_progress(job_id, "running", 
                                      f"[RETRY] {i+1}/{len(failed_urls)} retries completed - Recovered {len(retry_results)} additional MCQs", 
                                      processed_links=len(links), mcqs_found=len(all_results) + len(retry_results))
            
            all_results.extend(retry_results)
            print(f"🎉 RETRY PHASE COMPLETE: {len(retry_results)} additional MCQs recovered!")
        
        # Close resources
        if scraping_method == "screenshot":
            await screenshot_manager.close()
        else:
            await http_scraper.close()
        await stealth_manager.close_all_sessions()
        
        if not all_results:
            update_job_progress(job_id, "completed", 
                              f"[COMPLETE] No relevant MCQs found for '{topic}' and exam type '{exam_type}' across {len(links)} links after aggressive retry.", 
                              total_links=len(links), processed_links=len(links), mcqs_found=0)
            return
        
        # PHASE 3: Generate PDF
        success_count = len(all_results)
        failed_count = len(failed_urls) - len([r for r in all_results if r.get('from_retry')])
        
        final_message = f"[STEALTH SUCCESS] Found {success_count} MCQs from {len(links)} links using {scraping_method.upper()} method (recovered {len([r for r in all_results if r.get('from_retry', False)])} from retries)"
        update_job_progress(job_id, "running", final_message + " - Generating PDF...", 
                          total_links=len(links), processed_links=len(links), mcqs_found=success_count)
        
        try:
            # Convert results to MCQData objects
            mcqs = []
            for result in all_results:
                if isinstance(result, dict) and result.get('question'):
                    mcq = MCQData(
                        question=result['question'],
                        options=result.get('options', []),
                        answer=result.get('answer', ''),
                        exam_source_heading=result.get('exam_source_heading', ''),
                        exam_source_title=result.get('exam_source_title', ''),
                        is_relevant=result.get('is_relevant', True),
                        screenshot=result.get('screenshot', None)
                    )
                    mcqs.append(mcq)
            
            filename = generate_pdf(mcqs, topic, job_id, success_count, failed_count, len(links), pdf_format)
            
            # Copy to /app/ for user visibility
            pdf_path = get_pdf_directory() / filename
            app_pdf_path = Path("/app") / filename
            
            try:
                import shutil
                shutil.copy2(str(pdf_path), str(app_pdf_path))
                print(f"📋 PDF copied to /app/ for user visibility: {filename}")
            except Exception as e:
                print(f"⚠️ Could not copy PDF to /app/: {e}")
            
            # Handle S3 upload for Lambda
            if LAMBDA_S3_INTEGRATION:
                pdf_url = upload_pdf_to_s3_lambda(str(pdf_path), job_id, topic, exam_type)
                if not pdf_url:
                    pdf_url = f"/api/download-pdf/{filename}"
            else:
                pdf_url = f"/api/download-pdf/{filename}"
            
            generated_pdfs[job_id] = {
                "filename": filename,
                "topic": topic,
                "exam_type": exam_type,
                "mcqs_count": len(mcqs),
                "generated_at": datetime.now()
            }
            
            success_message = f"🎉 STEALTH MISSION ACCOMPLISHED! Generated {pdf_format.upper()} PDF with {len(mcqs)} MCQs for '{topic}' ({exam_type}) using advanced anti-rate-limiting system. No MCQ left behind!"
            update_job_progress(job_id, "completed", success_message, 
                              total_links=len(links), processed_links=len(links), 
                              mcqs_found=len(mcqs), pdf_url=pdf_url)
            
            print(f"🏆 Job {job_id} completed with {len(mcqs)} MCQs using stealth {scraping_method} processing")
            
        except Exception as e:
            print(f"❌ Error generating PDF: {e}")
            update_job_progress(job_id, "error", f"[ERROR] Error generating PDF: {str(e)}")
    
    except Exception as e:
        print(f"❌ Critical error in stealth extraction: {e}")
        update_job_progress(job_id, "error", f"[ERROR] Critical stealth error: {str(e)}")
        # Close resources on error
        try:
            if scraping_method == "screenshot":
                await screenshot_manager.close()
            else:
                await http_scraper.close()
            await stealth_manager.close_all_sessions()
        except:
            pass

# FastAPI App Configuration
app = FastAPI(title="Lambda MCQ Scraper", version="4.0.0")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.get("/")
async def read_root():
    """Root endpoint"""
    return {
        "message": "Lambda MCQ Scraper API with Puppeteer Screenshot Support", 
        "version": "4.0.0",
        "approach": "lambda_http_requests_scraping_with_puppeteer_screenshots",
        "browser_required": PUPPETEER_AVAILABLE,
        "screenshot_support": PUPPETEER_AVAILABLE,
        "concurrent_processes": 30,
        "status": "running",
        "supported_formats": ["text", "image"],
        "available_endpoints": [
            "GET /api/health",
            "POST /api/generate-mcq-pdf",
            "GET /api/job-status/{job_id}",
            "GET /api/download-pdf/{filename}",
            "GET /api/test-search"
        ]
    }

@app.get("/api/health")
async def health_check():
    """Health check endpoint with Puppeteer screenshot support"""
    try:
        scraper_stats = http_scraper.get_stats()
        screenshot_stats = screenshot_manager.get_stats()
        
        return {
            "status": "healthy",
            "version": "4.0.0",
            "approach": "lambda_http_requests_scraping_with_puppeteer_screenshots",
            "browser_required": False,
            "lambda_compatible": True,
            "concurrent_processes": http_scraper.max_concurrent_processes,
            "scraping_status": scraper_stats,
            "screenshot_status": screenshot_stats,
            "puppeteer_available": PUPPETEER_AVAILABLE,
            "active_jobs": len(persistent_storage.jobs),
            "s3_integration": LAMBDA_S3_INTEGRATION,
            "api_keys_available": len(api_key_manager.api_keys),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "4.0.0",
            "approach": "lambda_http_requests_scraping_with_puppeteer_screenshots",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/api/test-search")
async def test_search_functionality():
    """Test search functionality"""
    try:
        # Test search with sample data
        test_urls = await search_google_custom("Mathematics", "SSC")
        
        return {
            "status": "success",
            "api_keys_available": len(api_key_manager.api_keys),
            "search_engine_id": SEARCH_ENGINE_ID,
            "test_search_results": {
                "urls_found": len(test_urls),
                "sample_urls": test_urls[:3] if test_urls else [],
                "concurrent_processing": "30 processes"
            },
            "scraper_stats": http_scraper.get_stats()
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "api_keys_available": len(api_key_manager.api_keys)
        }

@app.post("/api/generate-mcq-pdf")
async def generate_mcq_pdf(request: SearchRequest, background_tasks: BackgroundTasks):
    """Generate MCQ PDF with concurrent processing"""
    job_id = str(uuid.uuid4())
    
    # Validate inputs
    if not request.topic.strip():
        raise HTTPException(status_code=400, detail="Topic is required")
    
    if request.exam_type not in ["SSC", "BPSC"]:
        raise HTTPException(status_code=400, detail="Exam type must be SSC or BPSC")
    
    if request.pdf_format not in ["text", "image"]:
        raise HTTPException(status_code=400, detail="PDF format must be 'text' or 'image'")
    
    # Initialize job progress
    update_job_progress(
        job_id, 
        "running", 
        f"[STARTING] Starting Lambda {request.exam_type} MCQ extraction for '{request.topic}' with 30 concurrent processes..."
    )
    
    # Start background task
    background_tasks.add_task(
        process_mcq_extraction,
        job_id=job_id,
        topic=request.topic.strip(),
        exam_type=request.exam_type,
        pdf_format=request.pdf_format
    )
    
    return {"job_id": job_id, "status": "started"}

@app.get("/api/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get job status"""
    job_data = persistent_storage.get_job(job_id)
    
    if not job_data:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobStatus(**job_data)

@app.get("/api/download-pdf/{filename}")
async def download_pdf(filename: str):
    """Download generated PDF"""
    try:
        pdf_dir = get_pdf_directory()
        file_path = pdf_dir / filename
        
        if not file_path.exists():
            print(f"❌ PDF file not found: {file_path}")
            raise HTTPException(status_code=404, detail="PDF file not found")
        
        print(f"✅ Serving PDF file: {file_path}")
        return FileResponse(
            path=str(file_path),
            filename=filename,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error serving PDF file: {e}")
        raise HTTPException(status_code=500, detail="Error serving PDF file")

@app.get("/api/cleanup-old-jobs")
async def cleanup_old_jobs():
    """Manual cleanup of old jobs"""
    try:
        persistent_storage.cleanup_old_jobs()
        return {"message": "Old jobs cleaned up successfully", "remaining_jobs": len(persistent_storage.jobs)}
    except Exception as e:
        print(f"❌ Error cleaning up old jobs: {e}")
        raise HTTPException(status_code=500, detail="Error cleaning up old jobs")

print("============================================================")
print("LAMBDA MCQ SCRAPER - HTTP ENHANCED VERSION")
print("============================================================")
print("✅ 30 Concurrent Processes Support")
print("✅ Lambda-Compatible HTTP Scraping")
print("✅ Beautiful PDF Generation")
print("✅ Testbook.com Integration")
print("✅ S3 Storage Support")
print("============================================================")