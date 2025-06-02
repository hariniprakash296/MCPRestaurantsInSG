from mcp.server.fastmcp import FastMCP
import requests
import os
from dotenv import load_dotenv
from typing import List, Dict, Union

# Load API key from environment
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
BASE_URL = "https://places.googleapis.com/v1/places:searchText"

# Create FastMCP server (NOT FastAPI)
mcp = FastMCP("Google Places Restaurant Finder")

def search_restaurants_logic(query: str) -> List[Dict[str, Union[str, int, float]]]:
    """
    Core logic for searching restaurants
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

@mcp.tool()
def search_restaurants(query: str) -> str:
    """
    Search for restaurants or food places in Singapore using a query like 'laksa' or 'vegan tiramisu'.
    Returns information about restaurants including names, addresses, price levels, and ratings.
    
    Args:
        query: The search query (e.g., 'laksa', 'vegan tiramisu', 'italian food')
    
    Returns:
        A formatted string with restaurant information
    """
    results = search_restaurants_logic(query)
    
    # Check if there's an error in the results
    if results and "error" in results[0]:
        return f"Error: {results[0]['error']}"
    
    if results and "message" in results[0]:
        return results[0]["message"]
    
    # Format the results nicely
    formatted_results = []
    for i, restaurant in enumerate(results, 1):
        formatted_results.append(
            f"{i}. {restaurant['name']}\n"
            f"   Address: {restaurant['address']}\n"
            f"   Price Level: {restaurant['price_level']}\n"
            f"   Rating: {restaurant['rating']}\n"
        )
    
    return "\n".join(formatted_results)

# The MCP server object must be available at module level
# This is what the MCP runtime is looking for
if __name__ == "__main__":
    # For MCP servers, you typically don't run them directly
    # They are started by the MCP client
    print("This is an MCP server. It should be started by an MCP client, not run directly.")