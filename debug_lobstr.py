import os
import requests
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("debug_lobstr")

def test_create_squid_flow():
    api_key = os.getenv("LOBSTR_API_KEY")
    if not api_key:
        logger.error("LOBSTR_API_KEY not found")
        return

    headers = {"Authorization": f"Token {api_key}"}
    
    # 1. List Store Crawlers to find the ID for Instagram Profile Scraper
    logger.info("1. Listing store crawlers to find 'Instagram Profile Scraper'...")
    # There isn't a documented endpoint to SEARCH crawlers easily, but let's try 
    # listing them or guessing the slug. 
    # The user provided "instagram-profile-scraper" which is likely the slug. 
    # API might accept slug for creation.
    
    # Create a squid using the real crawler hash ID
    logger.info("2. Attempting to create a Squid from crawler hash ID...")

    create_url = "https://api.lobstr.io/v1/squids"
    payload = {
        "crawler": "5c55ffbf10fbbc7ee63ebb2bcaa332b7",
        "name": "Test SDK Squid"
    }
    
    resp = requests.post(create_url, json=payload, headers=headers)
    logger.info(f"Create Squid Status: {resp.status_code}")
    logger.info(f"Create Squid Response: {resp.text}")
    
    if resp.status_code == 201 or resp.status_code == 200:
        squid_data = resp.json()
        squid_id = squid_data.get("id")
        logger.info(f"✅ Created Squid ID: {squid_id}")
        
        # 3. Now try to RUN this squid
        logger.info(f"3. Attempting to RUN Squid {squid_id}...")
        run_url = "https://api.lobstr.io/v1/runs"
        run_payload = {
            "squid": squid_id,
            "url": "https://instagram.com/cristiano" # Try 'url' param? Or usually strictly structured?
        }
        # Note: Store crawlers usually take specific inputs. 
        # For Instagram profile, it might be 'url' or 'urls' or something else.
        # But let's try the generic "url" first.
        
        resp_run = requests.post(run_url, json=run_payload, headers=headers)
        logger.info(f"Run Status: {resp_run.status_code}")
        logger.info(f"Run Response: {resp_run.text}")

if __name__ == "__main__":
    test_create_squid_flow()
