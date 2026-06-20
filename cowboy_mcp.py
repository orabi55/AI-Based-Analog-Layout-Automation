import sys
import json
import urllib.request
import urllib.parse
import warnings
from mcp.server.fastmcp import FastMCP

# Hard-suppress any standard environment warnings from leaking into stdout
warnings.filterwarnings("ignore")

# Initialize the FastMCP server engine
mcp = FastMCP("prompt-cowboy-local")

def fetch_optimization(raw_text: str) -> str:
    """Helper function to run the web request directly."""
    try:
        url = "https://www.promptcowboy.ai/api/generate"
        payload = json.dumps({"prompt": raw_text}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return res_data.get("optimized_prompt", "[Could not extract optimized prompt configuration metadata.]")
            
    except Exception as e:
        # Seamless local analog layout fallback if the server network times out
        return (
            f"### [Cowboy Cloud Routing Offline - Applying Local Architecture Blueprint]\n\n"
            f"**Expert Profile**: Senior Analog and Mixed-Signal Layout Design Architect\n"
            f"**Task Objective**: Implement automated generation framework for: `{raw_text}`\n"
            f"**Constraints**: Optimize layout geometry for matched parasitics, guard rings, and common-centroid symmetry configurations."
        )

@mcp.tool()
def forge_prompt_with_cowboy(raw_prompt: str) -> str:
    """
    Transforms a raw task, design concept, or prompt string into a hyper-structured, 
    highly optimized engineering prompt template using promptcowboy.ai optimization algorithms.
    """
    return fetch_optimization(raw_prompt)

if __name__ == "__main__":
    # Launch the stdio server channel natively
    mcp.run(transport='stdio')