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
        "status": "running",
        "available_endpoints": [
            "GET /api/health",
            "POST /api/scrape-mcqs",
            "GET /api/job-status/{job_id}",
            "GET /api/download-pdf/{job_id}"
        ]
    }

@app.get("/api/test-search")
async def test_search_functionality():
    """Test search functionality and API configuration"""
    try:
        # Test API key availability
        api_status = {
            "api_keys_available": len(api_key_manager.api_keys) > 0,
            "total_api_keys": len(api_key_manager.api_keys),
            "search_engine_id": SEARCH_ENGINE_ID
        }
        
        # Test a simple search
        test_urls = await search_mcq_urls("Mathematics", "SSC", 3)
        
        return {
            "status": "success",
            "api_configuration": api_status,
            "test_search_results": {
                "urls_found": len(test_urls),
                "sample_urls": test_urls[:3] if test_urls else [],
                "search_method": "google_custom_search" if api_status["api_keys_available"] else "fallback_urls"
            }
        }
        
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "api_configuration": {
                "api_keys_available": False,
                "total_api_keys": 0,
                "search_engine_id": SEARCH_ENGINE_ID
            }
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

@app.post("/api/scrape-mcqs")
async def scrape_mcqs(request: ScrapeRequest, background_tasks: BackgroundTasks):
    """Start MCQ scraping job"""
    try:
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Initialize job in persistent storage
        persistent_storage.update_job(
            job_id=job_id,
            status="started",
            progress="Initializing MCQ scraping job...",
            total_links=0,
            processed_links=0,
            mcqs_found=0
        )
        
        # Start background scraping task
        background_tasks.add_task(
            process_mcq_scraping_job,
            job_id=job_id,
            topic=request.topic,
            exam_type=request.exam_type,
            max_mcqs=request.max_mcqs
        )
        
        return JobResponse(
            job_id=job_id,
            status="started",
            message=f"MCQ scraping job started for topic '{request.topic}' and exam type '{request.exam_type}'"
        )
        
    except Exception as e:
        logger.error(f"Error starting MCQ scraping job: {e}")
        raise HTTPException(status_code=500, detail=f"Error starting scraping job: {str(e)}")

@app.get("/api/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get job status and progress"""
    try:
        job_data = persistent_storage.get_job(job_id)
        
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")
        
        return JobStatusResponse(**job_data)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job status: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting job status: {str(e)}")

@app.get("/api/download-pdf/{job_id}")
async def download_pdf(job_id: str):
    """Download generated PDF"""
    try:
        job_data = persistent_storage.get_job(job_id)
        
        if not job_data:
            raise HTTPException(status_code=404, detail="Job not found")
        
        pdf_url = job_data.get('pdf_url')
        if not pdf_url:
            raise HTTPException(status_code=404, detail="PDF not available yet")
        
        # For Lambda with S3 integration, return the S3 URL
        return {"download_url": pdf_url}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading PDF: {e}")
        raise HTTPException(status_code=500, detail=f"Error downloading PDF: {str(e)}")

# Background task function
async def process_mcq_scraping_job(job_id: str, topic: str, exam_type: str, max_mcqs: int):
    """Process MCQ scraping job in background"""
    try:
        # Initialize HTTP scraper
        if not http_scraper.is_initialized:
            http_scraper.initialize()
        
        # Update job status
        persistent_storage.update_job(
            job_id=job_id,
            status="searching",
            progress="Searching for MCQ sources...",
            total_links=0,
            processed_links=0,
            mcqs_found=0
        )
        
        # Search for MCQ URLs using Google Custom Search
        search_urls = await search_mcq_urls(topic, exam_type, max_mcqs)
        
        if not search_urls:
            persistent_storage.update_job(
                job_id=job_id,
                status="failed",
                progress="No MCQ sources found",
                total_links=0,
                processed_links=0,
                mcqs_found=0
            )
            return
        
        # Update job with search results
        persistent_storage.update_job(
            job_id=job_id,
            status="scraping",
            progress="Scraping MCQs from sources...",
            total_links=len(search_urls),
            processed_links=0,
            mcqs_found=0
        )
        
        # Scrape MCQs from URLs
        mcqs = []
        processed_count = 0
        
        for url in search_urls:
            try:
                mcq_data = await http_scraper.scrape_mcq_page(url, topic, exam_type)
                if mcq_data:
                    mcqs.append(mcq_data)
                
                processed_count += 1
                
                # Update progress
                persistent_storage.update_job(
                    job_id=job_id,
                    status="scraping",
                    progress=f"Scraped {processed_count}/{len(search_urls)} sources...",
                    total_links=len(search_urls),
                    processed_links=processed_count,
                    mcqs_found=len(mcqs)
                )
                
                # Stop if we have enough MCQs
                if len(mcqs) >= max_mcqs:
                    break
                    
            except Exception as e:
                logger.error(f"Error scraping URL {url}: {e}")
                continue
        
        if not mcqs:
            persistent_storage.update_job(
                job_id=job_id,
                status="failed",
                progress="No MCQs found",
                total_links=len(search_urls),
                processed_links=processed_count,
                mcqs_found=0
            )
            return
        
        # Generate PDF
        persistent_storage.update_job(
            job_id=job_id,
            status="generating_pdf",
            progress="Generating PDF...",
            total_links=len(search_urls),
            processed_links=processed_count,
            mcqs_found=len(mcqs)
        )
        
        pdf_url = await generate_mcq_pdf(mcqs, topic, exam_type, job_id)
        
        if pdf_url:
            persistent_storage.update_job(
                job_id=job_id,
                status="completed",
                progress="PDF generated successfully",
                total_links=len(search_urls),
                processed_links=processed_count,
                mcqs_found=len(mcqs),
                pdf_url=pdf_url
            )
        else:
            persistent_storage.update_job(
                job_id=job_id,
                status="failed",
                progress="PDF generation failed",
                total_links=len(search_urls),
                processed_links=processed_count,
                mcqs_found=len(mcqs)
            )
            
    except Exception as e:
        logger.error(f"Error processing MCQ scraping job {job_id}: {e}")
        persistent_storage.update_job(
            job_id=job_id,
            status="failed",
            progress=f"Job failed: {str(e)}",
            total_links=0,
            processed_links=0,
            mcqs_found=0
        )

# Helper functions
async def search_mcq_urls(topic: str, exam_type: str, max_mcqs: int) -> List[str]:
    """Search for MCQ URLs using Google Custom Search with fallback"""
    try:
        # First try Google Custom Search API
        urls = await try_google_custom_search(topic, exam_type, max_mcqs)
        
        if urls:
            logger.info(f"Found {len(urls)} URLs via Google Custom Search")
            return urls
        
        # Fallback to predefined MCQ sites
        logger.warning("Google Custom Search failed, using fallback URLs")
        return get_fallback_mcq_urls(topic, exam_type, max_mcqs)
        
    except Exception as e:
        logger.error(f"Error in search_mcq_urls: {e}")
        return get_fallback_mcq_urls(topic, exam_type, max_mcqs)

async def try_google_custom_search(topic: str, exam_type: str, max_mcqs: int) -> List[str]:
    """Try Google Custom Search API"""
    try:
        # Check if API key is available
        if not api_key_manager.api_keys:
            logger.warning("No Google API keys available")
            return []
        
        # Construct search query
        query = f"{topic} MCQ {exam_type} questions answers"
        
        # Use Google Custom Search API
        api_key = api_key_manager.get_current_key()
        search_url = "https://www.googleapis.com/customsearch/v1"
        
        params = {
            'key': api_key,
            'cx': SEARCH_ENGINE_ID,
            'q': query,
            'num': min(max_mcqs, 10)  # Max 10 results per request
        }
        
        logger.info(f"Searching Google Custom Search for: {query}")
        response = requests.get(search_url, params=params, timeout=15)
        
        if response.status_code == 403:
            logger.error("Google API quota exceeded or invalid API key")
            return []
        
        response.raise_for_status()
        data = response.json()
        
        urls = []
        for item in data.get('items', []):
            urls.append(item['link'])
        
        logger.info(f"Google Custom Search returned {len(urls)} URLs")
        return urls
        
    except Exception as e:
        logger.error(f"Google Custom Search error: {e}")
        return []

def get_fallback_mcq_urls(topic: str, exam_type: str, max_mcqs: int) -> List[str]:
    """Get fallback MCQ URLs when Google Search fails"""
    # Popular MCQ websites with topic-specific URLs
    fallback_sites = [
        f"https://www.examveda.com/{topic.lower()}-mcq-questions-answers/",
        f"https://www.indiabix.com/{topic.lower()}-questions-and-answers/",
        f"https://www.freshersworld.com/{topic.lower()}-questions-and-answers/",
        f"https://www.geeksforgeeks.org/{topic.lower()}-multiple-choice-questions/",
        f"https://www.javatpoint.com/{topic.lower()}-mcq",
        f"https://www.sanfoundry.com/{topic.lower()}-questions-answers/",
        f"https://www.tutorialspoint.com/{topic.lower()}-questions-answers/",
        f"https://www.studytonight.com/{topic.lower()}-mcqs/",
        f"https://www.careerride.com/{topic.lower()}-mcqs/",
        f"https://www.youth4work.com/{topic.lower()}-mcq-questions/"
    ]
    
    # Filter by exam type if SSC
    if exam_type.lower() == "ssc":
        ssc_sites = [
            f"https://www.sscadda.com/{topic.lower()}-questions-for-ssc-exams/",
            f"https://www.oliveboard.in/ssc/{topic.lower()}-questions/",
            f"https://www.testbook.com/ssc/{topic.lower()}-questions/",
            f"https://www.gradeup.co/ssc/{topic.lower()}-questions/",
            f"https://www.jagran.com/ssc/{topic.lower()}-mcq/"
        ]
        fallback_sites.extend(ssc_sites)
    
    # Return limited number of URLs
    return fallback_sites[:min(max_mcqs, 10)]

async def generate_mcq_pdf(mcqs: List[dict], topic: str, exam_type: str, job_id: str) -> Optional[str]:
    """Generate PDF from MCQs"""
    try:
        # Get PDF directory
        pdf_dir = get_pdf_directory()
        
        # Create PDF filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf_filename = f"mcq_{topic.replace(' ', '_')}_{exam_type}_{timestamp}.pdf"
        pdf_path = pdf_dir / pdf_filename
        
        # Generate PDF using ReportLab
        doc = SimpleDocTemplate(str(pdf_path), pagesize=A4)
        story = []
        styles = getSampleStyleSheet()
        
        # Title
        title = Paragraph(f"MCQ Questions - {topic} ({exam_type})", styles['Title'])
        story.append(title)
        story.append(Spacer(1, 20))
        
        # Add MCQs
        for i, mcq in enumerate(mcqs, 1):
            # Question
            question = Paragraph(f"<b>Q{i}.</b> {mcq['question']}", styles['Normal'])
            story.append(question)
            story.append(Spacer(1, 10))
            
            # Options
            for j, option in enumerate(mcq.get('options', []), 1):
                option_text = Paragraph(f"  {chr(64+j)}. {option}", styles['Normal'])
                story.append(option_text)
            
            story.append(Spacer(1, 10))
            
            # Answer
            if mcq.get('answer'):
                answer = Paragraph(f"<b>Answer:</b> {mcq['answer']}", styles['Normal'])
                story.append(answer)
            
            story.append(Spacer(1, 20))
        
        # Build PDF
        doc.build(story)
        
        # Upload to S3 if in Lambda environment
        if LAMBDA_S3_INTEGRATION:
            pdf_url = upload_pdf_to_s3_lambda(str(pdf_path), job_id, topic, exam_type)
            return pdf_url
        else:
            return str(pdf_path)
            
    except Exception as e:
        logger.error(f"Error generating PDF: {e}")
        return None

print("============================================================")
print("MCQ SCRAPER - HTTP SCRAPING FINAL VERSION")
print("============================================================")