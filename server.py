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

from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.colors import HexColor, black, darkblue, darkgreen, darkred, white, lightgrey
from reportlab.graphics.shapes import Drawing, Rect, Line
from reportlab.platypus.flowables import Flowable

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
        """Generate screenshot simulation for Lambda environment"""
        try:
            # Create a simple text-based representation as base64 image
            # This replaces the actual screenshot functionality for Lambda
            from PIL import Image, ImageDraw, ImageFont
            
            # Create a simple image representation
            img = Image.new('RGB', (800, 600), color='white')
            draw = ImageDraw.Draw(img)
            
            # Add URL text
            try:
                # Try to use a default font
                font = ImageFont.load_default()
            except:
                font = None
            
            draw.text((10, 10), f"URL: {url}", fill='black', font=font)
            draw.text((10, 30), "Lambda HTTP Scraping", fill='blue', font=font)
            draw.text((10, 50), "Screenshot simulation", fill='gray', font=font)
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            buffer.seek(0)
            
            screenshot_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            return screenshot_base64
            
        except Exception as e:
            print(f"⚠️ Error generating screenshot simulation: {e}")
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
    
    async def close(self):
        """Close HTTP session"""
        if self.session:
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
def generate_pdf(mcqs: List[MCQData], topic: str, job_id: str, relevant_mcqs: int, irrelevant_mcqs: int, total_links: int) -> str:
    """Generate BEAUTIFUL PROFESSIONAL PDF - Lambda compatible"""
    try:
        pdf_dir = get_pdf_directory()
        
        filename = f"Testbook_MCQs_{topic.replace(' ', '_')}_{job_id}.pdf"
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
        
        print(f"✅ BEAUTIFUL PROFESSIONAL PDF generated successfully: {filename} with {len(mcqs)} MCQs")
        return filename
        
    except Exception as e:
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
    """Process extraction with 30 concurrent workers"""
    try:
        await http_scraper.initialize()
        
        print(f"🚀 Starting concurrent processing with {http_scraper.max_concurrent_processes} workers: {len(links)} links")
        
        # Create concurrent tasks
        tasks = []
        for i, url in enumerate(links):
            task = http_scraper.scrape_testbook_page_concurrent(url, topic, exam_type)
            tasks.append(task)
        
        # Process in batches to avoid overwhelming the system
        batch_size = 30
        all_results = []
        
        for i in range(0, len(tasks), batch_size):
            batch_tasks = tasks[i:i+batch_size]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
            
            # Filter successful results
            successful_results = [r for r in batch_results if r is not None and not isinstance(r, Exception)]
            all_results.extend(successful_results)
            
            # Update progress
            processed_count = min(i + batch_size, len(links))
            update_job_progress(job_id, "running", 
                              f"[PROCESSING] Processed {processed_count}/{len(links)} links - Found {len(all_results)} relevant MCQs", 
                              processed_links=processed_count, mcqs_found=len(all_results))
            
            print(f"✅ Batch {i//batch_size + 1} completed: {len(successful_results)} MCQs found")
        
        await http_scraper.close()
        
        if not all_results:
            update_job_progress(job_id, "completed", 
                              f"[ERROR] No relevant MCQs found for '{topic}' and exam type '{exam_type}' across {len(links)} links.", 
                              total_links=len(links), processed_links=len(links), mcqs_found=0)
            return
        
        # Generate PDF
        final_message = f"[SUCCESS] Concurrent processing complete! Found {len(all_results)} relevant MCQs from {len(links)} total links."
        update_job_progress(job_id, "running", final_message + " Generating PDF...", 
                          total_links=len(links), processed_links=len(links), mcqs_found=len(all_results))
        
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
                        is_relevant=result.get('is_relevant', True)
                    )
                    mcqs.append(mcq)
            
            filename = generate_pdf(mcqs, topic, job_id, len(all_results), 0, len(links))
            
            # Handle S3 upload for Lambda
            if LAMBDA_S3_INTEGRATION:
                pdf_path = get_pdf_directory() / filename
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
            
            success_message = f"[DONE] SUCCESS! Generated PDF with {len(mcqs)} relevant MCQs for topic '{topic}' and exam type '{exam_type}' using {http_scraper.max_concurrent_processes} concurrent processes."
            update_job_progress(job_id, "completed", success_message, 
                              total_links=len(links), processed_links=len(links), 
                              mcqs_found=len(mcqs), pdf_url=pdf_url)
            
            print(f"✅ Job {job_id} completed successfully with {len(mcqs)} MCQs using concurrent processing")
            
        except Exception as e:
            print(f"❌ Error generating PDF: {e}")
            update_job_progress(job_id, "error", f"[ERROR] Error generating PDF: {str(e)}")
    
    except Exception as e:
        print(f"❌ Critical error in concurrent extraction: {e}")
        update_job_progress(job_id, "error", f"[ERROR] Critical error: {str(e)}")
        await http_scraper.close()

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
        "message": "Lambda MCQ Scraper API", 
        "version": "4.0.0",
        "approach": "lambda_http_requests_scraping",
        "browser_required": False,
        "concurrent_processes": 30,
        "status": "running",
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
    """Health check endpoint"""
    try:
        scraper_stats = http_scraper.get_stats()
        return {
            "status": "healthy",
            "version": "4.0.0",
            "approach": "lambda_http_requests_scraping",
            "browser_required": False,
            "lambda_compatible": True,
            "concurrent_processes": http_scraper.max_concurrent_processes,
            "scraping_status": scraper_stats,
            "active_jobs": len(persistent_storage.jobs),
            "s3_integration": LAMBDA_S3_INTEGRATION,
            "api_keys_available": len(api_key_manager.api_keys),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return {
            "status": "error",
            "version": "4.0.0",
            "approach": "lambda_http_requests_scraping",
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