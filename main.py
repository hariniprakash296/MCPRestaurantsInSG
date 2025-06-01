from mcp.server.fastmcp import FastMCP
import requests
from typing import List, Dict, Union
import os
from dotenv import load_dotenv

# Load API key from environment
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
BASE_URL = "https://places.googleapis.com/v1/places:searchText"

# Create the MCP server with HTTP transport
mcp = FastMCP("GooglePlacesRestaurantLocator")

@mcp.tool()
def search_restaurants(query: str) -> List[Dict[str, Union[str, int, float]]]:
    """
    Search for restaurants or food places in Singapore using a query like 'laksa' or 'vegan tiramisu'.
    Returns a list of place names, addresses, price level, and ratings if available.
   
    Args:
        query: Search term for food or restaurant type
       
    Returns:
        List of dictionaries containing restaurant information
    """
    if not GOOGLE_API_KEY:
        return [{"error": "Google Places API key not configured"}]
   
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.priceLevel,places.rating"
    }
   
    payload = {
        "textQuery": f"{query} in Singapore",
        "maxResultCount": 10
    }
   
    try:
        response = requests.post(BASE_URL, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
       
        data = response.json()
        results = []
       
        for place in data.get("places", []):
            name = place.get("displayName", {}).get("text", "Unknown")
            address = place.get("formattedAddress", "No address")
            price_level = place.get("priceLevel", "Unknown")
            rating = place.get("rating", "No rating")
           
            results.append({
                "name": name,
                "address": address,
                "price_level": str(price_level),
                "rating": str(rating)
            })
       
        return results if results else [{"message": "No places found for your query."}]
       
    except requests.exceptions.RequestException as e:
        return [{"error": f"API request failed: {str(e)}"}]
    except Exception as e:
        return [{"error": f"Unexpected error: {str(e)}"}]

# Add health check endpoint for Smithery
@mcp.get("/health")
def health_check():
    return {"status": "healthy", "service": "Google Places Restaurant Locator"}

if __name__ == "__main__":
    print("🚀 Starting MCP Server for Smithery deployment...")
    # Smithery requires HTTP transport on port 8000
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="http", port=port, host="0.0.0.0")