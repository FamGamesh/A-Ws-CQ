"""
AWS Lambda CORS Handler - Add this to your Lambda deployment
"""

import json
from typing import Dict, Any

def lambda_cors_response(status_code: int, body: Dict[Any, Any]) -> Dict[str, Any]:
    """
    Create a proper Lambda response with CORS headers for API Gateway
    """
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent',
            'Access-Control-Expose-Headers': '*',
            'Content-Type': 'application/json'
        },
        'body': json.dumps(body)
    }

def lambda_handler(event, context):
    """
    Main Lambda handler with CORS support
    """
    
    # Handle preflight OPTIONS requests
    if event.get('httpMethod') == 'OPTIONS':
        return lambda_cors_response(200, {'message': 'CORS preflight successful'})
    
    try:
        # Import your FastAPI app
        from server import app
        from mangum import Mangum
        
        # Create Mangum adapter with CORS
        handler = Mangum(app, enable_lifespan=False)
        
        # Process the request
        response = handler(event, context)
        
        # Ensure CORS headers are added to response
        if 'headers' not in response:
            response['headers'] = {}
            
        response['headers'].update({
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent',
            'Access-Control-Expose-Headers': '*'
        })
        
        return response
        
    except Exception as e:
        # Return error with CORS headers
        return lambda_cors_response(500, {
            'error': str(e),
            'message': 'Internal server error'
        })

# Alternative: If using direct Lambda integration (not Mangum)
def direct_lambda_handler(event, context):
    """
    Direct Lambda handler without FastAPI/Mangum
    """
    
    # Handle preflight
    if event.get('httpMethod') == 'OPTIONS':
        return lambda_cors_response(200, {'message': 'CORS preflight successful'})
    
    try:
        # Parse request
        path = event.get('path', '/')
        method = event.get('httpMethod', 'GET')
        body = event.get('body')
        
        if body:
            try:
                body = json.loads(body)
            except:
                pass
        
        # Handle different endpoints
        if path == '/' and method == 'GET':
            return lambda_cors_response(200, {
                "message": "Lambda MCQ Scraper API with CORS",
                "version": "4.0.0",
                "cors_enabled": True,
                "status": "running"
            })
        
        elif path == '/api/health' and method == 'GET':
            return lambda_cors_response(200, {
                "status": "healthy",
                "cors_enabled": True,
                "puppeteer_available": True
            })
        
        elif path == '/api/generate-mcq-pdf' and method == 'POST':
            # Your PDF generation logic here
            import uuid
            job_id = str(uuid.uuid4())
            
            return lambda_cors_response(200, {
                "job_id": job_id,
                "status": "started",
                "cors_enabled": True
            })
        
        else:
            return lambda_cors_response(404, {
                "error": "Not found",
                "path": path,
                "method": method
            })
            
    except Exception as e:
        return lambda_cors_response(500, {
            'error': str(e)
        })