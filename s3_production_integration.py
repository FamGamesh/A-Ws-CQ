"""
Production S3 Integration for Lambda
Complete S3 storage management for MCQ Scraper
"""

import os
import boto3
import json
import logging
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ProductionS3Manager:
    """Production-grade S3 manager for Lambda deployment"""
    
    def __init__(self):
        """Initialize S3 client with production configuration"""
        try:
            self.s3_client = boto3.client('s3', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
            self.pdf_bucket = os.environ.get('PDF_STORAGE_BUCKET', 'mcq-scraper-pdfs')
            self.region = os.environ.get('AWS_REGION', 'us-east-1')
            
            # Test S3 connection
            self.s3_client.head_bucket(Bucket=self.pdf_bucket)
            logger.info(f"✅ Production S3 Manager initialized - Bucket: {self.pdf_bucket}")
            
        except Exception as e:
            logger.error(f"❌ Error initializing Production S3 Manager: {e}")
            self.s3_client = None
    
    def upload_pdf_to_s3(self, local_file_path: str, job_id: str, topic: str, exam_type: str = "SSC") -> Optional[str]:
        """
        Upload PDF to S3 with production-grade configuration
        Returns public download URL
        """
        if not self.s3_client:
            logger.error("❌ S3 client not available")
            return None
            
        try:
            # Validate input file
            if not os.path.exists(local_file_path):
                logger.error(f"❌ Local file does not exist: {local_file_path}")
                return None
            
            # Generate production S3 key structure
            filename = Path(local_file_path).name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            
            # Clean names for URL safety
            clean_topic = self._clean_for_url(topic)
            clean_exam_type = self._clean_for_url(exam_type)
            
            s3_key = f"pdfs/{clean_exam_type}/{clean_topic}/{timestamp}_{job_id}/{filename}"
            
            logger.info(f"📤 Uploading PDF to S3: {s3_key}")
            
            # Get file size for logging
            file_size = os.path.getsize(local_file_path)
            
            # Upload with comprehensive metadata (removed ACL for bucket compatibility)
            self.s3_client.upload_file(
                local_file_path,
                self.pdf_bucket,
                s3_key,
                ExtraArgs={
                    'ContentType': 'application/pdf',
                    'ContentDisposition': f'attachment; filename="{filename}"',
                    'CacheControl': 'max-age=31536000',  # Cache for 1 year
                    'Metadata': {
                        'job-id': job_id,
                        'topic': clean_topic,
                        'exam-type': clean_exam_type,
                        'generated-by': 'mcq-scraper-lambda',
                        'upload-timestamp': timestamp,
                        'file-size': str(file_size),
                        'generator-version': '1.0.0'
                    }
                }
            )
            
            # Generate public URL
            public_url = f"https://{self.pdf_bucket}.s3.{self.region}.amazonaws.com/{s3_key}"
            
            logger.info(f"✅ PDF uploaded successfully: {public_url} ({file_size} bytes)")
            
            # Clean up local file immediately to save Lambda storage
            try:
                os.remove(local_file_path)
                logger.info("🗑️ Local file cleaned up successfully")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Could not clean up local file: {cleanup_error}")
            
            return public_url
            
        except Exception as e:
            logger.error(f"❌ Error uploading PDF to S3: {e}")
            return None
    
    def generate_presigned_download_url(self, s3_key: str, filename: str = None, expiration: int = 3600) -> Optional[str]:
        """
        Generate presigned URL for secure PDF download with custom filename
        """
        if not self.s3_client:
            return None
            
        try:
            params = {
                'Bucket': self.pdf_bucket,
                'Key': s3_key
            }
            
            # Add custom filename if provided
            if filename:
                params['ResponseContentDisposition'] = f'attachment; filename="{filename}"'
            
            presigned_url = self.s3_client.generate_presigned_url(
                'get_object',
                Params=params,
                ExpiresIn=expiration
            )
            
            logger.info(f"✅ Generated presigned URL (expires in {expiration}s)")
            return presigned_url
            
        except Exception as e:
            logger.error(f"❌ Error generating presigned URL: {e}")
            return None
    
    def list_pdfs_by_topic(self, topic: str, exam_type: str = "SSC", limit: int = 100) -> List[Dict]:
        """
        List recent PDFs for a specific topic and exam type
        """
        if not self.s3_client:
            return []
            
        try:
            clean_topic = self._clean_for_url(topic)
            clean_exam_type = self._clean_for_url(exam_type)
            prefix = f"pdfs/{clean_exam_type}/{clean_topic}/"
            
            response = self.s3_client.list_objects_v2(
                Bucket=self.pdf_bucket,
                Prefix=prefix,
                MaxKeys=limit
            )
            
            pdfs = []
            for obj in response.get('Contents', []):
                # Get object metadata
                try:
                    metadata_response = self.s3_client.head_object(
                        Bucket=self.pdf_bucket,
                        Key=obj['Key']
                    )
                    metadata = metadata_response.get('Metadata', {})
                except:
                    metadata = {}
                
                pdfs.append({
                    'key': obj['Key'],
                    'size': obj['Size'],
                    'last_modified': obj['LastModified'].isoformat(),
                    'public_url': f"https://{self.pdf_bucket}.s3.{self.region}.amazonaws.com/{obj['Key']}",
                    'job_id': metadata.get('job-id', 'unknown'),
                    'topic': metadata.get('topic', topic),
                    'exam_type': metadata.get('exam-type', exam_type),
                    'file_size_mb': round(obj['Size'] / (1024 * 1024), 2)
                })
            
            # Sort by last modified (newest first)
            pdfs.sort(key=lambda x: x['last_modified'], reverse=True)
            
            logger.info(f"✅ Found {len(pdfs)} PDFs for topic '{topic}' and exam '{exam_type}'")
            return pdfs
            
        except Exception as e:
            logger.error(f"❌ Error listing PDFs: {e}")
            return []
    
    def cleanup_old_pdfs(self, days_old: int = 30) -> int:
        """
        Clean up PDFs older than specified days
        Returns number of files deleted
        """
        if not self.s3_client:
            return 0
            
        try:
            cutoff_date = datetime.now() - timedelta(days=days_old)
            
            # List all objects
            response = self.s3_client.list_objects_v2(
                Bucket=self.pdf_bucket,
                Prefix="pdfs/"
            )
            
            objects_to_delete = []
            for obj in response.get('Contents', []):
                if obj['LastModified'].replace(tzinfo=None) < cutoff_date:
                    objects_to_delete.append({'Key': obj['Key']})
            
            if objects_to_delete:
                # Delete in batches of 1000 (S3 limit)
                deleted_count = 0
                for i in range(0, len(objects_to_delete), 1000):
                    batch = objects_to_delete[i:i+1000]
                    self.s3_client.delete_objects(
                        Bucket=self.pdf_bucket,
                        Delete={'Objects': batch}
                    )
                    deleted_count += len(batch)
                
                logger.info(f"🗑️ Cleaned up {deleted_count} old PDFs (older than {days_old} days)")
                return deleted_count
            else:
                logger.info("ℹ️ No old PDFs to clean up")
                return 0
                
        except Exception as e:
            logger.error(f"❌ Error cleaning up old PDFs: {e}")
            return 0
    
    def _clean_for_url(self, text: str) -> str:
        """Clean text for safe URL usage"""
        if not text:
            return "unknown"
        
        # Keep only alphanumeric characters, spaces, hyphens, and underscores
        cleaned = "".join(c for c in text if c.isalnum() or c in (' ', '-', '_')).strip()
        
        # Replace spaces with hyphens and convert to lowercase
        cleaned = cleaned.replace(' ', '-').lower()
        
        # Remove consecutive hyphens
        while '--' in cleaned:
            cleaned = cleaned.replace('--', '-')
        
        # Ensure not empty
        return cleaned if cleaned else "unknown"

# Global S3 manager instance
production_s3_manager = ProductionS3Manager()

def get_pdf_directory() -> Path:
    """
    Production Lambda PDF directory function
    Always uses /tmp with S3 integration for persistence
    """
    # Lambda environment - use /tmp for temporary storage
    pdf_dir = Path("/tmp/pdfs")
    
    try:
        pdf_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ Production PDF directory ready: {pdf_dir}")
    except Exception as e:
        logger.error(f"❌ Error creating PDF directory: {e}")
        # Fallback to /tmp root
        pdf_dir = Path("/tmp")
    
    return pdf_dir

def upload_pdf_to_s3_lambda(local_filepath: str, job_id: str, topic: str, exam_type: str = "SSC") -> Optional[str]:
    """
    Production function to upload PDF to S3 and return download URL
    """
    try:
        return production_s3_manager.upload_pdf_to_s3(local_filepath, job_id, topic, exam_type)
    except Exception as e:
        logger.error(f"❌ Error in upload_pdf_to_s3_lambda: {e}")
        return None

# Lambda environment detection
LAMBDA_S3_INTEGRATION = True