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
from bs4 import BeautifulSoup
import time

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
    try:
        from s3_lambda_integration import get_pdf_directory, upload_pdf_to_s3_lambda
        LAMBDA_S3_INTEGRATION = True
        print("✅ Lambda S3 integration loaded")
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

# HTTP SCRAPING MANAGER - Lambda Compatible Solution
class HTTPScrapingManager:
    """
    HTTP-based scraping manager - No browser required
    Perfect for AWS Lambda environments
    """
    
    def __init__(self):
        self.session = None
        self.is_initialized = False
        self.scraping_stats = {
            "requests_made": 0,
            "successful_scrapes": 0,
            "failed_scrapes": 0
        }
        
    def initialize(self):
        """Initialize HTTP session with optimal settings"""
        if self.is_initialized:
            return
            
        try:
            self.session = requests.Session()
            
            # Optimize session for scraping
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            })
            
            # Configure session for reliability
            adapter = requests.adapters.HTTPAdapter(
                max_retries=requests.adapters.Retry(
                    total=3,
                    backoff_factor=1,
                    status_forcelist=[502, 503, 504, 429]
                )
            )
            
            self.session.mount('http://', adapter)
            self.session.mount('https://', adapter)
            
            self.is_initialized = True
            print("✅ HTTP Scraping Manager initialized successfully")
            
        except Exception as e:
            print(f"❌ Error initializing HTTP Scraping Manager: {e}")
            raise
    
    async def scrape_mcq_page(self, url: str, topic: str, exam_type: str = "SSC") -> Optional[dict]:
        """Scrape MCQ page using HTTP requests and BeautifulSoup"""
        try:
            if not self.is_initialized:
                self.initialize()
            
            print(f"🔍 HTTP scraping: {url}")
            self.scraping_stats["requests_made"] += 1
            
            # Fetch page content
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            
            # Parse HTML content
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract MCQ data using CSS selectors
            mcq_data = self._extract_mcq_from_soup(soup, url, topic, exam_type)
            
            if mcq_data:
                self.scraping_stats["successful_scrapes"] += 1
                print(f"✅ Successfully scraped MCQ from {url}")
                return mcq_data
            else:
                self.scraping_stats["failed_scrapes"] += 1
                print(f"❌ No MCQ data found on {url}")
                return None
                
        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout scraping {url}")
            self.scraping_stats["failed_scrapes"] += 1
            return None
        except requests.exceptions.RequestException as e:
            print(f"❌ Request error scraping {url}: {e}")
            self.scraping_stats["failed_scrapes"] += 1
            return None
        except Exception as e:
            print(f"❌ Error scraping {url}: {e}")
            self.scraping_stats["failed_scrapes"] += 1
            return None
    
    def _extract_mcq_from_soup(self, soup: BeautifulSoup, url: str, topic: str, exam_type: str) -> Optional[dict]:
        """Extract MCQ data from parsed HTML"""
        try:
            # Extract question
            question = ""
            question_selectors = [
                'h1.questionBody.tag-h1',
                'div.questionBody', 
                'h1.question',
                '.question-text',
                '[class*="question"]'
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
            
            # Extract options
            options = []
            option_selectors = [
                'li.option',
                '.option',
                '[class*="option"]',
                'ul li'
            ]
            
            for selector in option_selectors:
                option_elements = soup.select(selector)
                if option_elements:
                    for elem in option_elements:
                        option_text = self._clean_text(elem.get_text())
                        if option_text and len(option_text.strip()) > 0:
                            options.append(option_text)
                    break
            
            # Extract answer/solution
            answer = ""
            answer_selectors = [
                '.solution',
                '.answer',
                '[class*="solution"]',
                '[class*="answer"]'
            ]
            
            for selector in answer_selectors:
                answer_elem = soup.select_one(selector)
                if answer_elem:
                    answer = self._clean_text(answer_elem.get_text())
                    break
            
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
            
            # Return MCQ data if we have at least question and some content
            if question and (options or answer):
                return {
                    "url": url,
                    "question": question,
                    "options": options,
                    "answer": answer,
                    "exam_source_heading": exam_source_heading,
                    "exam_source_title": exam_source_title,
                    "is_relevant": True,
                    "scraping_method": "http_requests"
                }
            else:
                print(f"❌ Insufficient MCQ data extracted from {url}")
                return None
                
        except Exception as e:
            print(f"❌ Error extracting MCQ data from {url}: {e}")
            return None
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text"""
        if not text:
            return ""
        
        # Remove extra whitespace and clean up
        text = ' '.join(text.split())
        
        # Remove common unwanted patterns
        unwanted_patterns = [
            r'\s*\n\s*',
            r'\s*\r\s*',
            r'\s*\t\s*',
            r'^\s*[-•]\s*',  # Remove bullet points
            r'\s+',  # Multiple spaces
        ]
        
        for pattern in unwanted_patterns:
            text = re.sub(pattern, ' ', text)
        
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
    
    def get_stats(self) -> dict:
        """Get scraping statistics"""
        return {
            "initialized": self.is_initialized,
            "scraping_stats": self.scraping_stats,
            "success_rate": (
                self.scraping_stats["successful_scrapes"] / max(self.scraping_stats["requests_made"], 1) * 100
            )
        }

# Global HTTP scraping manager
http_scraper = HTTPScrapingManager()

# PERSISTENT JOB STORAGE
class PersistentJobStorage:
    """Persistent storage for job progress"""
    
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

# API KEY MANAGEMENT
class APIKeyManager:
    """API Key management for Google Custom Search"""
    
    def __init__(self):
        self.api_keys = []
        self.current_key_index = 0
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
        
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        
        if self.current_key_index == 0:
            return None
        
        return self.api_keys[self.current_key_index]
    
    def get_remaining_keys(self) -> int:
        """Get number of remaining keys"""
        return len(self.api_keys) - self.current_key_index - 1

# Global API key manager
api_key_manager = APIKeyManager()

# Environment variables
SEARCH_ENGINE_ID = os.environ.get('SEARCH_ENGINE_ID', '2701a7d64a00d47fd')

# FastAPI App Configuration
app = FastAPI(title="MCQ Scraper API", version="4.0.0")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
class MCQData(BaseModel):
    question: str
    options: List[str]
    answer: str
    exam_source_heading: str = ""
    exam_source_title: str = ""
    is_relevant: bool = True

class ScrapeRequest(BaseModel):
    topic: str
    exam_type: str = "SSC"
    max_mcqs: int = 50

class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: str
    total_links: int = 0
    processed_links: int = 0
    mcqs_found: int = 0
    pdf_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

# API Endpoints
@app.get("/")
async def read_root():
    """Root endpoint"""
    return {
        "message": "MCQ Scraper API", 
        "version": "4.0.0",
        "approach": "http_requests_scraping",
        "browser_required": False,
        "status": "running"
    }

@app.get("/api/health")
async def health_check():
    """Health check endpoint with HTTP scraping status"""
    try:
        from health_check import get_health_status
        return await get_health_status()
    except Exception as e:
        return {
            "status": "error",
            "version": "4.0.0",
            "approach": "http_requests_scraping",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }

print("============================================================")
print("MCQ SCRAPER - HTTP SCRAPING FINAL VERSION")
print("============================================================")