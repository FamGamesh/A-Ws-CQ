"""
AWS Lambda CORS Handler - FIXED VERSION
Proper CORS implementation for API Gateway integration
"""

import json
from typing import Dict, Any

def lambda_cors_response(status_code: int, body: Dict[Any, Any]) -> Dict[str, Any]:
    """
    Create a proper Lambda response with CORS headers for API Gateway - FIXED VERSION
    """
    return {
        'statusCode': status_code,
        'headers': {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent, X-Requested-With, Accept, Origin',
            'Access-Control-Allow-Credentials': 'false',  # CRITICAL FIX: Must be 'false' when using '*' for origin
            'Access-Control-Expose-Headers': 'Content-Type, Authorization',
            'Access-Control-Max-Age': '86400',
            'Content-Type': 'application/json'
        },
        'body': json.dumps(body)
    }

def lambda_handler(event, context):
    """
    Main Lambda handler with fixed CORS support
    """
    
    # Handle preflight OPTIONS requests
    if event.get('httpMethod') == 'OPTIONS':
        return lambda_cors_response(200, {'message': 'CORS preflight successful', 'cors_fixed': True})
    
    try:
        # Import your FastAPI app (use fixed version)
        from server_fixed import app
        from mangum import Mangum
        
        # Create Mangum adapter with proper CORS configuration
        handler = Mangum(
            app, 
            lifespan="off",
            api_gateway_base_path="/prod"
        )
        
        # Process the request
        response = handler(event, context)
        
        # Ensure CORS headers are added to response (FIXED VERSION)
        if 'headers' not in response:
            response['headers'] = {}
            
        response['headers'].update({
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, X-Amz-Date, Authorization, X-Api-Key, X-Amz-Security-Token, X-Amz-User-Agent, X-Requested-With, Accept, Origin',
            'Access-Control-Allow-Credentials': 'false',  # CRITICAL FIX: Consistent with allow_origins=["*"]
            'Access-Control-Expose-Headers': 'Content-Type, Authorization',
            'Access-Control-Max-Age': '86400'
        })
        
        return response
        
    except Exception as e:
        # Return error with CORS headers
        return lambda_cors_response(500, {
            'error': str(e),
            'message': 'Internal server error',
            'cors_fixed': True
        })

# Alternative: If using direct Lambda integration (not Mangum)
def direct_lambda_handler(event, context):
    """
    Direct Lambda handler without FastAPI/Mangum - FIXED VERSION
    """
    
    # Handle preflight
    if event.get('httpMethod') == 'OPTIONS':
        return lambda_cors_response(200, {'message': 'CORS preflight successful', 'cors_fixed': True})
    
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
                "message": "Lambda MCQ Scraper API with CORS - FIXED",
                "version": "4.0.1",
                "cors_enabled": True,
                "cors_fixed": True,
                "status": "running"
            })
        
        elif path == '/api/health' and method == 'GET':
            return lambda_cors_response(200, {
                "status": "healthy",
                "cors_enabled": True,
                "cors_fixed": True,
                "puppeteer_available": True
            })
        
        elif path == '/api/generate-mcq-pdf' and method == 'POST':
            # Your PDF generation logic here
            import uuid
            job_id = str(uuid.uuid4())
            
            return lambda_cors_response(200, {
                "job_id": job_id,
                "status": "started",
                "cors_enabled": True,
                "cors_fixed": True
            })
        
        else:
            return lambda_cors_response(404, {
                "error": "Not found",
                "path": path,
                "method": method,
                "cors_fixed": True
            })
            
    except Exception as e:
        return lambda_cors_response(500, {
            'error': str(e),
            'cors_fixed': True
        })