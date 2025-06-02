"""
MCP (Model Context Protocol) Streamable HTTP Server for Singapore Restaurant Search

MCP is a protocol that allows AI assistants to connect to external tools and data sources.
This server implements the MCP specification using HTTP transport, allowing AI clients 
to search for restaurants in Singapore using the Google Places API.

Key MCP Concepts:
- Server: Provides tools/resources to AI clients
- Client: AI assistant that uses the server's capabilities  
- Tools: Functions the server exposes (like search_restaurants)
- Transport: How messages are sent (HTTP in this case)
- JSON-RPC: Message format used by MCP
"""

import os
import json
import asyncio
import requests
from typing import Any, Dict, Optional, List, Union
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from urllib.parse import parse_qs
import uuid
from dotenv import load_dotenv

# Load environment variables from .env file (contains API keys)
load_dotenv()

# Create FastAPI application (HTTP server framework)
app = FastAPI()

# Add CORS (Cross-Origin Resource Sharing) middleware for web security
# This allows the server to accept requests from web browsers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # Allow requests from any domain (be more restrictive in production)
    allow_credentials=True,     # Allow cookies and authentication headers
    allow_methods=["GET", "POST", "DELETE"],  # HTTP methods this server supports
    allow_headers=["*"],        # Allow any headers in requests
)

# Global storage for MCP sessions and server configuration
# Sessions track individual AI client connections
sessions = {}
# Server configuration comes from query parameters (like API keys)
server_config = {}

# Google Places API configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")  # Load API key from environment
BASE_URL = "https://places.googleapis.com/v1/places:searchText"  # Google API endpoint

def parse_query_config(query_string: str) -> Dict[str, Any]:
    """
    Parse configuration from URL query parameters using dot notation.
    
    MCP clients can send configuration like: ?apiKey=secret123&server.host=localhost
    This function converts that into a nested dictionary structure.
    
    Example: "apiKey=abc123&server.port=8080" becomes:
    {"apiKey": "abc123", "server": {"port": "8080"}}
    """
    # Parse URL query string into key-value pairs
    params = parse_qs(query_string)
    config = {}
    
    # Process each parameter
    for key, values in params.items():
        if values:  # Only process if there's a value
            value = values[0]  # Take first value if multiple provided
            
            # Handle dot notation (server.host becomes nested structure)
            keys = key.split('.')  # Split "server.host" into ["server", "host"]
            current = config
            
            # Navigate/create nested structure
            for k in keys[:-1]:  # All keys except the last one
                if k not in current:
                    current[k] = {}  # Create nested dictionary if doesn't exist
                current = current[k]  # Move deeper into structure
            
            # Set the final value
            current[keys[-1]] = value
    
    return config

def validate_origin(request: Request) -> bool:
    """
    Security function to prevent DNS rebinding attacks.
    
    DNS rebinding is when malicious websites try to access local servers
    by tricking the browser. We check the Origin header to prevent this.
    """
    origin = request.headers.get("origin")  # Where the request came from
    host = request.headers.get("host", "localhost")  # What host was requested
    
    # For local development, allow localhost connections
    if host.startswith("localhost") or host.startswith("127.0.0.1"):
        return True
    
    # In production, you'd implement stricter origin validation
    # For now, we log and allow (should be more restrictive in production)
    print(f"Request from origin: {origin}, host: {host}")
    return True

async def create_sse_stream(messages: list):
    """
    Create Server-Sent Events (SSE) stream for real-time communication.
    
    SSE allows the server to send multiple messages to the client over a single
    HTTP connection. This is useful for streaming responses or sending multiple
    related messages.
    
    Each message is formatted according to SSE specification:
    id: unique-identifier
    data: json-message
    
    (blank line separates events)
    """
    for message in messages:
        event_id = str(uuid.uuid4())  # Generate unique ID for this event
        data = json.dumps(message)    # Convert message to JSON string
        # Format according to SSE specification
        yield f"id: {event_id}\ndata: {data}\n\n"

def search_restaurants_logic(query: str, api_key: str = None) -> List[Dict[str, Union[str, int, float]]]:
    """
    Core restaurant search logic using Google Places API.
    
    This is the actual business logic that searches for restaurants.
    It's separated from the MCP protocol handling so it can be reused.
    """
    # Use provided API key or fall back to environment variable
    google_api_key = api_key or GOOGLE_API_KEY
    
    if not google_api_key:
        return [{"error": "Google Places API key not configured"}]
   
    # Set up HTTP headers for Google Places API request
    headers = {
        "Content-Type": "application/json",           # We're sending JSON data
        "X-Goog-Api-Key": google_api_key,           # Authentication header
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.priceLevel,places.rating"  # What data we want back
    }
   
    # Request body for Google Places API
    payload = {
        "textQuery": f"{query} in Singapore",  # Search query with location constraint
        "maxResultCount": 10                   # Limit results to 10
    }
   
    try:
        # Make HTTP request to Google Places API
        response = requests.post(BASE_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()  # Raise exception if HTTP error occurred
       
        # Parse JSON response
        data = response.json()
        results = []
       
        # Process each restaurant in the response
        for place in data.get("places", []):
            # Extract information with fallback values if data missing
            name = place.get("displayName", {}).get("text", "Unknown")
            address = place.get("formattedAddress", "No address")
            price_level = place.get("priceLevel", "Unknown")
            rating = place.get("rating", "No rating")
           
            # Add to results list
            results.append({
                "name": name,
                "address": address,
                "price_level": str(price_level),
                "rating": str(rating)
            })
       
        # Return results or message if none found
        return results if results else [{"message": "No places found for your query."}]
       
    except requests.exceptions.RequestException as e:
        # Network or HTTP errors
        return [{"error": f"API request failed: {str(e)}"}]
    except Exception as e:
        # Any other unexpected errors
        return [{"error": f"Unexpected error: {str(e)}"}]

@app.post("/mcp")
async def handle_post_request(request: Request):
    """
    Handle POST requests to the MCP endpoint.
    
    This is the main entry point for MCP communication. AI clients send
    JSON-RPC messages via HTTP POST to interact with the server.
    
    MCP Protocol Flow:
    1. Client sends JSON-RPC message
    2. Server processes message
    3. Server returns response (either JSON or SSE stream)
    """
    
    # Security check to prevent malicious requests
    if not validate_origin(request):
        raise HTTPException(status_code=403, detail="Invalid origin")
    
    # Parse configuration from URL query parameters
    # Smithery passes configuration this way: ?apiKey=secret123
    query_string = str(request.url.query)
    if query_string:
        global server_config
        server_config.update(parse_query_config(query_string))
    
    # Get session ID from header if present
    # Sessions allow stateful communication between client and server
    session_id = request.headers.get("Mcp-Session-Id")
    
    # Parse the JSON-RPC message from request body
    try:
        body = await request.json()
    except:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    # Check what response formats the client accepts
    accept_header = request.headers.get("accept", "")
    supports_sse = "text/event-stream" in accept_header    # Can handle streaming responses
    supports_json = "application/json" in accept_header    # Can handle JSON responses
    
    # Handle both single messages and batches
    if isinstance(body, list):
        messages = body  # Already a list of messages
    else:
        messages = [body]  # Wrap single message in list
    
    # Check if any messages are requests (need responses) vs notifications (fire-and-forget)
    has_requests = any(msg.get("method") and "id" in msg for msg in messages)
    
    if not has_requests:
        # Only notifications/responses - acknowledge and return
        return Response(status_code=202)  # 202 = Accepted
    
    # Process requests and generate responses
    responses = []
    
    for msg in messages:
        # Handle MCP initialization
        if msg.get("method") == "initialize":
            """
            Initialize method is the first message in MCP protocol.
            It establishes capabilities and creates a session.
            """
            # Create new session for this client
            new_session_id = str(uuid.uuid4())
            sessions[new_session_id] = {"config": server_config.copy()}
            
            # Build initialization response according to MCP spec
            response = {
                "jsonrpc": "2.0",  # JSON-RPC version
                "id": msg.get("id"),  # Must match request ID
                "result": {
                    "protocolVersion": "2025-03-26",  # MCP protocol version we support
                    "capabilities": {
                        "tools": {}  # We support tools (like search_restaurants)
                    },
                    "serverInfo": {
                        "name": "Singapore Restaurant Locator",
                        "version": "1.0.0"
                    }
                }
            }
            responses.append(response)
            session_id = new_session_id  # Use new session ID for response header
            
        # Handle tools/list request  
        elif msg.get("method") == "tools/list":
            """
            List all tools available on this server.
            Tools are functions that AI clients can call.
            """
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "tools": [
                        {
                            "name": "search_restaurants",
                            "description": "Search for restaurants or food places in Singapore using queries like 'laksa' or 'vegan tiramisu'",
                            "inputSchema": {
                                # JSON Schema describing what parameters this tool accepts
                                "type": "object",
                                "properties": {
                                    "query": {
                                        "type": "string",
                                        "description": "Search term for food or restaurant type"
                                    }
                                },
                                "required": ["query"]  # Query parameter is mandatory
                            }
                        }
                    ]
                }
            }
            responses.append(response)
            
        # Handle tool execution requests
        elif msg.get("method") == "tools/call":
            """
            Execute a tool (function) with given parameters.
            This is where the actual work happens.
            """
            # Extract tool name and arguments from request
            tool_name = msg.get("params", {}).get("name")
            arguments = msg.get("params", {}).get("arguments", {})
            
            if tool_name == "search_restaurants":
                # Execute restaurant search
                query = arguments.get("query", "")
                
                # Get API key from configuration (passed via query params)
                api_key = server_config.get("apiKey")
                
                # Call our search logic
                search_results = search_restaurants_logic(query, api_key)
                
                # Check for errors in search results
                if search_results and "error" in search_results[0]:
                    error_msg = search_results[0]["error"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "error": {
                            "code": -32603,  # Internal error code
                            "message": error_msg
                        }
                    }
                elif search_results and "message" in search_results[0]:
                    # No results found
                    result_text = search_results[0]["message"]
                    response = {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": result_text
                                }
                            ]
                        }
                    }
                else:
                    # Format successful results
                    formatted_results = []
                    for i, restaurant in enumerate(search_results, 1):
                        formatted_results.append(
                            f"{i}. {restaurant['name']}\n"
                            f"   Address: {restaurant['address']}\n"
                            f"   Price Level: {restaurant['price_level']}\n"
                            f"   Rating: {restaurant['rating']}\n"
                        )
                    
                    result_text = "\n".join(formatted_results)
                    
                    response = {
                        "jsonrpc": "2.0",
                        "id": msg.get("id"),
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": result_text
                                }
                            ]
                        }
                    }
            else:
                # Unknown tool requested
                response = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {
                        "code": -32601,  # Method not found
                        "message": f"Unknown tool: {tool_name}"
                    }
                }
            responses.append(response)
        
        # Handle other MCP methods (notifications/ping/etc)
        else:
            # Unknown method
            response = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {
                    "code": -32601,  # Method not found
                    "message": f"Unknown method: {msg.get('method')}"
                }
            }
            responses.append(response)
    
    # Determine response format based on client capabilities
    if supports_sse:
        """
        Return Server-Sent Events stream.
        This allows for real-time streaming of responses.
        """
        headers = {}
        # Include session ID in response header if we created one
        if session_id and any(msg.get("method") == "initialize" for msg in messages):
            headers["Mcp-Session-Id"] = session_id
            
        return StreamingResponse(
            create_sse_stream(responses),  # Create SSE stream from responses
            media_type="text/event-stream",
            headers=headers
        )
    else:
        """
        Return regular JSON response.
        This is the traditional HTTP request-response pattern.
        """
        headers = {}
        # Include session ID in response header if we created one
        if session_id and any(msg.get("method") == "initialize" for msg in messages):
            headers["Mcp-Session-Id"] = session_id
            
        # Return single response or batch based on what was sent
        if len(responses) == 1:
            return Response(
                content=json.dumps(responses[0]),
                media_type="application/json",
                headers=headers
            )
        else:
            return Response(
                content=json.dumps(responses),
                media_type="application/json", 
                headers=headers
            )

@app.get("/mcp")
async def handle_get_request(request: Request):
    """
    Handle GET requests to the MCP endpoint.
    
    GET requests are used to open Server-Sent Events streams for server-initiated
    communication (like notifications or progress updates).
    """
    
    # Security check
    if not validate_origin(request):
        raise HTTPException(status_code=403, detail="Invalid origin")
    
    # Check if client accepts SSE streams
    accept_header = request.headers.get("accept", "")
    if "text/event-stream" not in accept_header:
        # Client doesn't support SSE, return Method Not Allowed
        raise HTTPException(status_code=405, detail="Method Not Allowed")
    
    # Create empty SSE stream (in full implementation, this might send notifications)
    async def empty_stream():
        """
        Generate an empty SSE stream.
        In a full implementation, this would:
        - Send server-initiated notifications
        - Send progress updates for long-running operations
        - Send real-time data updates
        """
        # Send empty data to keep connection alive
        yield "data: {}\n\n"
        
        # In real implementation, you might have:
        # while session_active:
        #     if has_notification:
        #         yield f"data: {json.dumps(notification)}\n\n"
        #     await asyncio.sleep(1)
    
    return StreamingResponse(empty_stream(), media_type="text/event-stream")

@app.delete("/mcp")
async def handle_delete_request(request: Request):
    """
    Handle DELETE requests to terminate MCP sessions.
    
    When a client is done, it can explicitly terminate its session
    to clean up server resources.
    """
    
    # Get session ID from request header
    session_id = request.headers.get("Mcp-Session-Id")
    
    if session_id and session_id in sessions:
        # Session exists, delete it
        del sessions[session_id]
        return Response(status_code=200)  # Success
    
    # Session not found
    return Response(status_code=404)  # Not Found

# Main entry point when running the server
if __name__ == "__main__":
    """
    Start the HTTP server.
    
    This creates a web server that listens for HTTP requests on the specified port.
    Smithery will start this server in a Docker container and route requests to it.
    """
    # Get port from environment variable (Smithery sets this)
    port = int(os.environ.get('PORT', 8000))
    
    # Start the server using uvicorn (ASGI server)
    uvicorn.run(
        app,                    # FastAPI application
        host="0.0.0.0",        # Listen on all network interfaces
        port=port,             # Port number
        log_level="info"       # Logging level
    )